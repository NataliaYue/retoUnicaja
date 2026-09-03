"""
Validación del flujo completo de Bizum: petición → contacto → PIN → envío.

Por qué existe: `tests/evaluar.py` NO cubre este camino. Sus cuatro preguntas
de "bizum" son consultas sobre bizums pasados ("¿cuánto le he enviado a
María?"), que se resuelven con SQL. Ninguna toca `extraer_peticion_bizum`,
`preparar_bizum_desde_backend`, `validar_pin_bizum` ni el contador de intentos.
Se puede romper el envío entero y el banco de evaluación seguiría dando 97 %.

Todo este flujo es lógica de backend, sin LLM, así que el test es determinista
y tarda segundos: no depende de que Ollama esté levantado ni de la varianza del
modelo. Es la red de seguridad para refactorizar `agent.py`, donde el flujo
está escrito dos veces (`preparar_bizum_desde_backend` y la rama del tool call).

La base de datos se copia antes de empezar y se restaura al final: los envíos
mueven saldo de verdad e insertan movimientos.

Uso:  python -m tests.test_bizum
"""

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agent import (  # noqa: E402
    Agente,
    buscar_contacto_bizum,
    extraer_peticion_bizum,
    validar_importe_bizum,
)
from backend.banking_api import api_consultar_saldo  # noqa: E402
from backend.config import BIZUM_LIMITE_DIARIO, BIZUM_MAX, BIZUM_MIN, DB_PATH, PIN_BIZUM  # noqa: E402


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


def comprueba(ok: bool, etiqueta: str, detalle: str = "") -> bool:
    print(f"  {'OK' if ok else 'XX'}  {etiqueta}")
    if not ok and detalle:
        print(f"        {detalle}")
    return ok


# ──────────────────────────────────────────────────────────────────────────
# 1. Reconocimiento de la petición (regex, sin LLM)
# ──────────────────────────────────────────────────────────────────────────

def test_extraccion() -> list[bool]:
    print("\n1. Reconocer la petición de Bizum")
    r = []

    ACEPTADAS = [
        ("Haz un bizum a María López de 20 euros", "María López", 20.0),
        ("Envía 20 euros a María López por bizum", "María López", 20.0),
        ("Envía 20 € por bizum a María López", "María López", 20.0),
        ("Haz un bizum de 25 a Ana", "Ana", 25.0),
        ("bizum de 20 euros para María", "María", 20.0),
        ("págale 15 euros a Ana por bizum", "Ana", 15.0),
        ("un bizum de 30 para Carlos", "Carlos", 30.0),
    ]
    for mensaje, destinatario, cantidad in ACEPTADAS:
        d = extraer_peticion_bizum(mensaje)
        ok = d is not None and d["destinatario"] == destinatario and d["cantidad"] == cantidad
        r.append(comprueba(ok, f"reconoce: {mensaje}", f"devolvió {d}"))

    # Los patrones llevan ^ a propósito: sin él enganchaban a mitad de frase y
    # una negación acababa pidiendo el PIN de un envío que el usuario rechazaba.
    RECHAZADAS = [
        "no quiero hacer un bizum de 20 euros para María",
        "mejor no, un bizum de 50 para Ana no",
        "¿cuánto le he enviado por bizum a María?",
        "¿cuáles son mis contactos de bizum?",
    ]
    for mensaje in RECHAZADAS:
        d = extraer_peticion_bizum(mensaje)
        r.append(comprueba(d is None, f"NO dispara: {mensaje}", f"devolvió {d}"))

    return r


# ──────────────────────────────────────────────────────────────────────────
# 2. Validación del importe (antes de pedir el PIN)
# ──────────────────────────────────────────────────────────────────────────

def test_limites_importe() -> list[bool]:
    print("\n2. Límites del importe")
    r = []
    for cantidad, valido in [
        (BIZUM_MIN - 0.01, False),
        (BIZUM_MIN, True),
        (20.0, True),
        (BIZUM_MAX, True),
        (BIZUM_MAX + 0.01, False),
    ]:
        error = validar_importe_bizum(cantidad)
        r.append(comprueba((error is None) == valido,
                           f"{cantidad:8.2f} € {'válido' if valido else 'rechazado'}",
                           f"devolvió {error!r}"))
    return r


# ──────────────────────────────────────────────────────────────────────────
# 3. Resolución del contacto
# ──────────────────────────────────────────────────────────────────────────

def test_contactos() -> list[bool]:
    print("\n3. Resolución del contacto")
    r = []
    contactos = api_consultar_saldo() and None  # fuerza que la BD esté accesible

    exacto = buscar_contacto_bizum("María López")
    r.append(comprueba(exacto["estado"] == "exacto", "nombre completo → exacto", str(exacto)))

    parcial = buscar_contacto_bizum("María")
    r.append(comprueba(parcial["estado"] in ("exacto", "sugerencia"),
                       "nombre parcial → sugerencia", str(parcial)))

    typo = buscar_contacto_bizum("Maria Lopez")  # sin tildes
    r.append(comprueba(typo["estado"] in ("exacto", "sugerencia"),
                       "sin tildes → resuelve", str(typo)))

    desconocido = buscar_contacto_bizum("Napoleón Bonaparte")
    r.append(comprueba(desconocido["estado"] == "no_encontrado",
                       "desconocido → no_encontrado", str(desconocido)))
    return r


# ──────────────────────────────────────────────────────────────────────────
# 4. El flujo completo, turno a turno
# ──────────────────────────────────────────────────────────────────────────

async def test_flujo() -> list[bool]:
    print("\n4. Flujo completo")
    r = []

    # --- Envío correcto: pide PIN, y con el PIN bueno mueve el dinero
    g = Grabadora()
    ag = Agente(g)
    saldo_antes = api_consultar_saldo()["saldo"]
    await ag.preparar_bizum_desde_backend({"destinatario": "María López", "cantidad": 20.0, "concepto": ""})
    r.append(comprueba(g.pidio_pin() and ag.bizum_pendiente is not None,
                       "importe válido → pide PIN y deja el envío pendiente"))

    g.limpiar()
    await ag.validar_pin_bizum(PIN_BIZUM)
    saldo_despues = api_consultar_saldo()["saldo"]
    r.append(comprueba(round(saldo_antes - saldo_despues, 2) == 20.0,
                       "PIN correcto → descuenta 20,00 € del saldo",
                       f"{saldo_antes} → {saldo_despues}"))
    r.append(comprueba(ag.bizum_pendiente is None, "tras enviar, no queda nada pendiente"))

    # --- Importe fuera de rango: NO debe pedir el PIN
    for cantidad in (2000.0, 0.0):
        g = Grabadora()
        ag = Agente(g)
        await ag.preparar_bizum_desde_backend({"destinatario": "María López", "cantidad": cantidad, "concepto": ""})
        r.append(comprueba(not g.pidio_pin() and ag.bizum_pendiente is None,
                           f"{cantidad:.2f} € → rechaza SIN pedir PIN",
                           f"eventos={g.tipos}"))

    # --- Contacto desconocido
    g = Grabadora()
    ag = Agente(g)
    await ag.preparar_bizum_desde_backend({"destinatario": "Napoleón Bonaparte", "cantidad": 20.0, "concepto": ""})
    r.append(comprueba(not g.pidio_pin() and "no encuentro" in g.texto.lower(),
                       "contacto desconocido → no pide PIN y lo dice", g.texto[:80]))

    # --- Sugerencia de contacto, aceptada y cancelada
    g = Grabadora()
    ag = Agente(g)
    await ag.preparar_bizum_desde_backend({"destinatario": "Maria Lop", "cantidad": 10.0, "concepto": ""})
    sugerido = ag.correccion_contacto_pendiente is not None
    if sugerido:
        g.limpiar()
        await ag.gestionar_correccion_contacto_pendiente("sí")
        r.append(comprueba(g.pidio_pin(), "acepta la sugerencia → pide PIN"))
    else:
        r.append(comprueba(ag.bizum_pendiente is not None, "resuelve el contacto directamente"))

    g = Grabadora()
    ag = Agente(g)
    await ag.preparar_bizum_desde_backend({"destinatario": "Maria Lop", "cantidad": 10.0, "concepto": ""})
    if ag.correccion_contacto_pendiente:
        g.limpiar()
        # Esta rama tumbaba el WebSocket entero: leía `bizum_pendiente`, que
        # aquí todavía es None. Es el bug de la Fase 0; que no vuelva.
        await ag.gestionar_correccion_contacto_pendiente("no")
        r.append(comprueba(ag.bizum_pendiente is None and ag.correccion_contacto_pendiente is None,
                           "rechaza la sugerencia → cancela sin reventar", g.texto[:80]))

    # --- PIN incorrecto: consume intentos y acaba cancelando
    g = Grabadora()
    ag = Agente(g)
    await ag.preparar_bizum_desde_backend({"destinatario": "María López", "cantidad": 15.0, "concepto": ""})
    intentos_iniciales = ag.intentos_pin_restantes
    saldo_antes = api_consultar_saldo()["saldo"]
    for _ in range(intentos_iniciales):
        g.limpiar()
        await ag.validar_pin_bizum("0000")
    r.append(comprueba(ag.bizum_pendiente is None,
                       f"{intentos_iniciales} PIN incorrectos → cancela el envío"))
    r.append(comprueba(api_consultar_saldo()["saldo"] == saldo_antes,
                       "PIN incorrecto → el saldo NO se mueve"))

    # --- Cancelar por chat con un envío pendiente
    g = Grabadora()
    ag = Agente(g)
    await ag.preparar_bizum_desde_backend({"destinatario": "María López", "cantidad": 12.0, "concepto": ""})
    g.limpiar()
    gestionado = await ag.gestionar_bizum_pendiente("cancela")
    r.append(comprueba(gestionado and ag.bizum_pendiente is None,
                       "'cancela' con envío pendiente → lo anula"))

    # --- El límite diario del asistente
    g = Grabadora()
    ag = Agente(g)
    await ag.preparar_bizum_desde_backend({"destinatario": "María López", "cantidad": BIZUM_MAX, "concepto": ""})
    g.limpiar()
    await ag.validar_pin_bizum(PIN_BIZUM)
    texto = g.texto.lower()
    r.append(comprueba("límite diario" in texto or "denegada" in texto,
                       f"supera el límite diario ({BIZUM_LIMITE_DIARIO:.0f} €) → lo deniega",
                       g.texto[:100]))

    return r


# ──────────────────────────────────────────────────────────────────────────

def main() -> int:
    copia = DB_PATH.with_suffix(".db.bak_test")
    shutil.copy2(DB_PATH, copia)
    print("=" * 62)
    print("FLUJO DE BIZUM  ·  sin LLM, determinista")
    print("=" * 62)
    try:
        r = test_extraccion() + test_limites_importe() + test_contactos()
        r += asyncio.run(test_flujo())
    finally:
        shutil.copy2(copia, DB_PATH)
        copia.unlink()
        print("\n(base de datos restaurada)")

    print("\n" + "=" * 62)
    print(f"RESULTADO: {sum(r)}/{len(r)} comprobaciones correctas")
    print("=" * 62)
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
