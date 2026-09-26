"""
Validación del flujo completo de Bizum: petición → contacto → PIN → envío.

Por qué existe: `tests/evaluar.py` NO cubre este camino. Sus cuatro preguntas
de "bizum" son consultas sobre bizums pasados ("¿cuánto le he enviado a
María?"), que se resuelven con SQL. Ninguna toca `extraer_peticion_bizum`,
`preparar_bizum_desde_backend`, `validar_pin_bizum` ni el contador de intentos.

Todo este flujo es lógica de backend, sin LLM, así que el test es determinista.

La base de datos se restaura al terminar desde la fixture de `conftest.py`.

Uso:  pytest tests/test_bizum.py
"""

import asyncio
import sqlite3
import threading

import pytest

from backend.agent import (
    Agente,
    buscar_contacto_bizum,
    extraer_peticion_bizum,
    validar_importe_bizum,
)
from backend.banking_api import api_consultar_saldo, api_enviar_bizum
from backend.config import BIZUM_LIMITE_DIARIO, BIZUM_MAX, BIZUM_MIN, DB_PATH, PIN_BIZUM
from backend.database import transaccion_escritura


class Grabadora:
    """Recoge los eventos que el agente emitiría por WebSocket."""

    def __init__(self):
        self.eventos: list[dict] = []

    async def __call__(self, evento: dict):
        self.eventos.append(evento)

    @property
    def tipos(self) -> list[str]:
        return [e["type"] for e in self.eventos]

    @property
    def texto(self) -> str:
        finales = [e for e in self.eventos if e["type"] == "fin_respuesta"]
        return finales[-1]["texto"] if finales else ""

    def pidio_pin(self) -> bool:
        return "pedir_pin" in self.tipos

    def limpiar(self):
        self.eventos.clear()


@pytest.fixture
def agente():
    """Un agente nuevo con su grabadora de eventos."""
    g = Grabadora()
    return g, Agente(g)


def pide_bizum(ag, destinatario, cantidad, concepto=""):
    return {"destinatario": destinatario, "cantidad": cantidad, "concepto": concepto}


# ──────────────────────────────────────────────────────────────────────────
# 1. Reconocimiento de la petición
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mensaje, destinatario, cantidad", [
    ("Haz un bizum a María López de 20 euros", "María López", 20.0),
    ("Envía 20 euros a María López por bizum", "María López", 20.0),
    ("Envía 20 € por bizum a María López", "María López", 20.0),
    ("Haz un bizum de 25 a Ana", "Ana", 25.0),
    ("bizum de 20 euros para María", "María", 20.0),
    ("págale 15 euros a Ana por bizum", "Ana", 15.0),
    ("un bizum de 30 para Carlos", "Carlos", 30.0),
])
def test_reconoce_la_peticion(mensaje, destinatario, cantidad):
    d = extraer_peticion_bizum(mensaje)
    assert d is not None, f"no reconoció la petición: {mensaje!r}"
    assert d["destinatario"] == destinatario, f"devolvió {d}"
    assert d["cantidad"] == cantidad, f"devolvió {d}"


# Los patrones llevan ^ a propósito: sin él enganchaban a mitad de frase y
# una negación acababa pidiendo el PIN de un envío que el usuario rechazaba.
@pytest.mark.parametrize("mensaje", [
    "no quiero hacer un bizum de 20 euros para María",
    "mejor no, un bizum de 50 para Ana no",
    "¿cuánto le he enviado por bizum a María?",
    "¿cuáles son mis contactos de bizum?",
])
def test_no_dispara_el_flujo(mensaje):
    d = extraer_peticion_bizum(mensaje)
    assert d is None, f"devolvió {d}"


# ──────────────────────────────────────────────────────────────────────────
# 2. Validación del importe
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cantidad, valido", [
    (BIZUM_MIN - 0.01, False),
    (BIZUM_MIN, True),
    (20.0, True),
    (BIZUM_MAX, True),
    (BIZUM_MAX + 0.01, False),
])
def test_limites_del_importe(cantidad, valido):
    error = validar_importe_bizum(cantidad)
    assert (error is None) == valido, f"{cantidad:.2f} € devolvió {error!r}"


# ──────────────────────────────────────────────────────────────────────────
# 3. Resolución del contacto
# ──────────────────────────────────────────────────────────────────────────

def test_contacto_nombre_completo():
    r = buscar_contacto_bizum("María López")
    assert r["estado"] == "exacto", str(r)


def test_contacto_nombre_parcial():
    r = buscar_contacto_bizum("María")
    assert r["estado"] in ("exacto", "sugerencia"), str(r)


def test_contacto_sin_tildes():
    r = buscar_contacto_bizum("Maria Lopez")
    assert r["estado"] in ("exacto", "sugerencia"), str(r)


def test_contacto_desconocido():
    r = buscar_contacto_bizum("Napoleón Bonaparte")
    assert r["estado"] == "no_encontrado", str(r)


# ──────────────────────────────────────────────────────────────────────────
# 4. El flujo completo, turno a turno
# ──────────────────────────────────────────────────────────────────────────

def test_importe_valido_pide_pin(agente):
    g, ag = agente
    asyncio.run(ag.preparar_bizum_desde_backend(pide_bizum(ag, "María López", 20.0)))
    assert g.pidio_pin(), f"eventos={g.tipos}"
    assert ag.bizum_pendiente is not None


def test_pin_correcto_descuenta_el_saldo(agente):
    g, ag = agente
    saldo_antes = api_consultar_saldo()["saldo"]

    async def envio():
        await ag.preparar_bizum_desde_backend(pide_bizum(ag, "María López", 20.0))
        g.limpiar()
        await ag.validar_pin_bizum(PIN_BIZUM)

    asyncio.run(envio())
    saldo_despues = api_consultar_saldo()["saldo"]

    assert round(saldo_antes - saldo_despues, 2) == 20.0, f"{saldo_antes} → {saldo_despues}"
    assert ag.bizum_pendiente is None, "tras enviar no debería quedar nada pendiente"


@pytest.mark.parametrize("cantidad", [2000.0, 0.0], ids=["2000 €", "0 €"])
def test_importe_fuera_de_rango_no_pide_pin(agente, cantidad):
    g, ag = agente
    asyncio.run(ag.preparar_bizum_desde_backend(pide_bizum(ag, "María López", cantidad)))
    assert not g.pidio_pin(), f"pidió el PIN para {cantidad:.2f} €: eventos={g.tipos}"
    assert ag.bizum_pendiente is None


def test_contacto_desconocido_no_pide_pin(agente):
    g, ag = agente
    asyncio.run(ag.preparar_bizum_desde_backend(pide_bizum(ag, "Napoleón Bonaparte", 20.0)))
    assert not g.pidio_pin(), f"eventos={g.tipos}"
    assert "no encuentro" in g.texto.lower(), g.texto[:80]


def test_sugerencia_aceptada_pide_pin(agente):
    g, ag = agente

    async def flujo():
        await ag.preparar_bizum_desde_backend(pide_bizum(ag, "Maria Lop", 10.0))
        if ag.correccion_contacto_pendiente is not None:
            g.limpiar()
            await ag.gestionar_correccion_contacto_pendiente("sí")
            return True
        return False

    hubo_sugerencia = asyncio.run(flujo())
    if hubo_sugerencia:
        assert g.pidio_pin(), f"eventos={g.tipos}"
    else:
        assert ag.bizum_pendiente is not None, "resolvió el contacto sin dejar el envío pendiente"


def test_sugerencia_rechazada_cancela_sin_reventar(agente):
    g, ag = agente

    async def flujo():
        await ag.preparar_bizum_desde_backend(pide_bizum(ag, "Maria Lop", 10.0))
        if ag.correccion_contacto_pendiente:
            g.limpiar()
            await ag.gestionar_correccion_contacto_pendiente("no")

    asyncio.run(flujo())
    assert ag.bizum_pendiente is None, g.texto[:80]
    assert ag.correccion_contacto_pendiente is None, g.texto[:80]


def test_pin_incorrecto_cancela_y_no_mueve_el_saldo(agente):
    g, ag = agente
    saldo_antes = api_consultar_saldo()["saldo"]

    async def flujo():
        await ag.preparar_bizum_desde_backend(pide_bizum(ag, "María López", 15.0))
        for _ in range(ag.intentos_pin_restantes):
            g.limpiar()
            await ag.validar_pin_bizum("0000")

    asyncio.run(flujo())

    assert ag.bizum_pendiente is None, "agotar los intentos debería cancelar el envío"
    assert api_consultar_saldo()["saldo"] == saldo_antes, "el saldo no debe moverse"


def test_cancela_por_chat_con_envio_pendiente(agente):
    g, ag = agente

    async def flujo():
        await ag.preparar_bizum_desde_backend(pide_bizum(ag, "María López", 12.0))
        g.limpiar()
        return await ag.gestionar_bizum_pendiente("cancela")

    gestionado = asyncio.run(flujo())
    assert gestionado, "no interceptó el mensaje de cancelación"
    assert ag.bizum_pendiente is None


def test_supera_el_limite_diario(agente):
    g, ag = agente

    async def flujo():
        await ag.preparar_bizum_desde_backend(pide_bizum(ag, "María López", BIZUM_MAX))
        g.limpiar()
        await ag.validar_pin_bizum(PIN_BIZUM)

    asyncio.run(flujo())
    texto = g.texto.lower()
    assert "límite diario" in texto or "denegada" in texto, (
        f"debería denegarlo por el límite de {BIZUM_LIMITE_DIARIO:.0f} €: {g.texto[:100]}"
    )


# ──────────────────────────────────────────────────────────────────────────
# 5. Atomicidad: dos envíos a la vez no pueden gastar el mismo saldo
# ──────────────────────────────────────────────────────────────────────────

def test_el_bloqueo_se_toma_antes_de_leer():
    """
    `api_enviar_bizum` es un leer-comprobar-escribir: lee el saldo, comprueba
    que llega, y solo entonces lo actualiza. Si esa secuencia no es atómica,
    dos envíos simultáneos leen el MISMO saldo, los dos pasan la comprobación
    y el segundo UPDATE pisa al primero.

    Aquí se comprueba la PROPIEDAD que lo impide, no el síntoma. Una carrera
    solo se manifiesta cuando los hilos coinciden en una ventana de
    milisegundos: un test que dependa de eso pasaría casi siempre aunque el
    arreglo se hubiera quitado.

    Si alguien cambiara el BEGIN IMMEDIATE por un BEGIN a secas, el bloqueo se
    tomaría en el primer UPDATE —o sea, después de las lecturas— y esta
    comprobación fallaría en el acto.
    """
    bloqueado = []

    def intenta_escribir():
        otra = sqlite3.connect(DB_PATH, isolation_level=None, timeout=0.5)
        try:
            otra.execute("BEGIN IMMEDIATE")
            bloqueado.append(False)     # pudo entrar: NO había bloqueo
            otra.rollback()
        except sqlite3.OperationalError:
            bloqueado.append(True)      # esperado: el bloqueo ya estaba tomado
        finally:
            otra.close()

    with transaccion_escritura() as conn:
        conn.execute("SELECT saldo FROM cliente WHERE id = 1").fetchone()
        hilo = threading.Thread(target=intenta_escribir)
        hilo.start()
        hilo.join()

    assert bloqueado == [True], (
        "otro escritor pudo entrar: el leer-comprobar-escribir no es atómico"
    )


def test_dos_envios_simultaneos_no_se_pisan():
    # El límite diario del asistente son 500 €, así que de dos envíos de 300 €
    # solo puede pasar uno, y el saldo tiene que bajar exactamente 300.
    with transaccion_escritura() as conn:
        conn.execute("UPDATE cliente SET saldo = 1000 WHERE id = 1")
        conn.execute("DELETE FROM movimientos "
                     "WHERE categoria = 'bizum_enviado' AND fecha = date('now')")

    salidas: list[dict] = []
    hilos = [threading.Thread(target=lambda: salidas.append(
        api_enviar_bizum("María López", 300.0))) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    aceptados = sum(1 for s in salidas if s.get("estado") == "ok")
    saldo = api_consultar_saldo()["saldo"]

    assert aceptados == 1, (
        f"con límite de {BIZUM_LIMITE_DIARIO:.0f} € pasaron {aceptados}: "
        f"{[s.get('estado') for s in salidas]}"
    )
    assert round(saldo, 2) == 700.0, f"quedó en {saldo} € en vez de 700,00 €"
