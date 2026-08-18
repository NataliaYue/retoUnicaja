"""
Analítica avanzada sobre el histórico de movimientos.

El reto nombra tres tipos de consulta compleja: gastos, comparativas y
**suscripciones**. Las dos primeras las resuelve el LLM escribiendo SQL. La
tercera no, porque "¿a qué estoy suscrito?" no es una consulta: es una
inferencia. Hay que descubrir qué cargos se repiten con regularidad, con qué
cadencia, si alguno ha cambiado de precio y si alguno ha dejado de cobrarse.
Nada de eso está escrito en ninguna columna.

Pedirle esa inferencia a un modelo de 8B en forma de SQL es poco fiable. Aquí
se hace de forma determinista en Python: el resultado es reproducible, el
algoritmo se puede explicar en la memoria, y el LLM se limita a redactar la
conclusión.

CRITERIO DE DETECCIÓN
---------------------
Lo que identifica un pago recurrente no es el importe. Un recibo de la luz
varía tanto como un repostaje (en los datos actuales, CV de 0,16 frente a
0,25): el importe no separa una cosa de la otra.

Lo que sí separa es la **regularidad de los intervalos** entre cargos. Netflix
se cobra cada 31 días clavados; en Repsol repostas cuando te toca. Midiendo la
desviación típica de los intervalos dividida entre su mediana:

    Netflix, Spotify, Digi, Gimnasio, Alquiler, Endesa ... 0,03
    Emasagra (bimestral) .............................. 0,01
    El más regular de los NO recurrentes ............... 0,12
    Supermercados, gasolineras, restaurantes .......... 0,25 - 3,34

Con eso solo no basta, porque la regularidad no significa nada cuando hay
pocos cargos: con dos, hay UN intervalo y su desviación típica es cero por
definición, así que dos compras sueltas separadas por medio año entraban como
"suscripción semestral". Por eso el criterio final son dos condiciones:

  1. Los intervalos son regulares (irregularidad <= 0,06) y su mediana encaja
     en una cadencia conocida con un margen del 20 %.
  2. Si hay menos de 3 intervalos, se exige además una señal independiente:
     que el importe sea siempre idéntico. Eso deja pasar la póliza anual del
     coche (dos cobros de 418,60 €) y descarta las coincidencias.

VALIDACIÓN
----------
Los umbrales se eligieron midiendo, no a ojo. `tests/test_recurrencia.py`
regenera el histórico con N semillas distintas y compara contra la verdad
conocida por construcción en `seed.py`. Sobre 500 semillas:

    Falsos negativos ................. 0
    Falsos positivos ................. 1 (0,2 % de los históricos)
    Cadencias mal clasificadas ....... 0
    Seguro anual detectado ........... 500/500

Dos decisiones salieron de esa medición: bajar la irregularidad de 0,10 a 0,06
(quitó 7 falsos positivos sin añadir ni un falso negativo) y, sobre todo,
ampliar `seed.py` de 15 a 24 meses. Lo segundo pesó más que cualquier umbral:
con más histórico los comercios aleatorios acumulan cargos suficientes para
que su irregularidad se note, y los falsos positivos pasaron de 23 a 0.
"""

from datetime import date
from statistics import median, pstdev

from .database import conexion_lectura

# Cadencias reconocidas: nombre, duración típica en días, cada cuántos meses se
# cobra (para el coste mensual equivalente) y cargos mínimos para afirmarla.
# Las cadencias largas se afirman con menos cargos porque ni en 24 meses de
# histórico caben más de dos o tres cobros.
_CADENCIAS = (
    ("mensual", 30, 1, 3),
    ("bimestral", 61, 2, 3),
    ("trimestral", 91, 3, 3),
    ("semestral", 182, 6, 2),
    ("anual", 365, 12, 2),
)

# Un intervalo mediano encaja en una cadencia si no se desvía más de esto.
_TOLERANCIA_CADENCIA = 0.20

# Y los intervalos deben ser regulares ENTRE SÍ. Este es el filtro que de
# verdad separa una suscripción de un comercio que simplemente visitas mucho.
# Valor elegido midiendo sobre 100 históricos con semillas distintas: bajarlo
# de 0,10 a 0,06 quita 7 falsos positivos sin añadir un solo falso negativo.
_MAX_IRREGULARIDAD = 0.06

# La regularidad solo significa algo si hay intervalos suficientes que comparar.
# Con dos cargos hay UN intervalo, su desviación típica es cero por definición y
# el filtro anterior lo aprueba siempre: así es como dos compras sueltas
# separadas por medio año acababan clasificadas como "suscripción semestral".
# Por debajo de este número de intervalos se exige una segunda señal
# independiente: que el importe sea siempre idéntico. Eso distingue una póliza
# anual de 418,60 € de dos compras de ropa que cayeron separadas por un año.
_MIN_INTERVALOS_SIN_CORROBORAR = 3

# Cuántos importes distintos admite un cargo de precio fijo. Uno solo es lo
# normal; dos o tres indican subidas de precio escalonadas. Muchos importes
# distintos significan que es un recibo variable (luz, agua) y ahí no tiene
# sentido hablar de "cambio de precio".
_MAX_IMPORTES_DISTINTOS = 3

# Margen antes de dar por cancelado un pago recurrente: si lleva más de 1,6
# ciclos sin cobrarse, probablemente ya no está activo.
_FACTOR_INACTIVIDAD = 1.6

# Suscripciones: servicios a los que el cliente se apunta y de los que puede
# darse de baja. Incluye la cuota del gimnasio (Netflix, Spotify, gimnasio).
# Todo lo demás que sea recurrente (alquiler, luz, agua, internet, seguro del
# coche) se trata como recibo fijo.
#
# Se entrega ya clasificado en vez de dejar que el modelo lo deduzca de la
# categoría: pidiéndoselo por prompt respondía "estás suscrito al alquiler".
_CATEGORIAS_SUSCRIPCION = frozenset({"suscripciones", "gimnasio"})


def _clasificar_cadencia(intervalos: list[int]) -> tuple[str, int, int] | None:
    """
    Devuelve (nombre, días, meses) de la cadencia si los intervalos son
    regulares y encajan en alguna conocida. None si el patrón no es recurrente.
    """
    if not intervalos:
        return None

    intervalo_tipico = median(intervalos)
    if intervalo_tipico <= 0:
        return None

    # Regularidad: es el filtro principal.
    irregularidad = pstdev(intervalos) / intervalo_tipico
    if irregularidad > _MAX_IRREGULARIDAD:
        return None

    for nombre, dias, meses, cargos_minimos in _CADENCIAS:
        if len(intervalos) + 1 < cargos_minimos:
            continue
        if abs(intervalo_tipico - dias) <= dias * _TOLERANCIA_CADENCIA:
            return nombre, dias, meses

    return None


def _analizar_comercio(comercio: str, movimientos: list[dict], hoy: date) -> dict | None:
    """Analiza los cargos de un comercio. None si no son recurrentes."""
    fechas = [date.fromisoformat(m["fecha"]) for m in movimientos]
    importes = [round(abs(m["importe"]), 2) for m in movimientos]

    if len(fechas) < 2:
        return None

    intervalos = [(fechas[i + 1] - fechas[i]).days for i in range(len(fechas) - 1)]

    cadencia = _clasificar_cadencia(intervalos)
    if cadencia is None:
        return None

    nombre_cadencia, dias_cadencia, meses_cadencia = cadencia

    # ¿Precio fijo o recibo variable?
    importes_distintos = sorted(set(importes))
    precio_fijo = len(importes_distintos) <= _MAX_IMPORTES_DISTINTOS

    # Con pocos intervalos, el ritmo por sí solo no es prueba (ver comentario
    # de _MIN_INTERVALOS_SIN_CORROBORAR): hace falta que el importe se repita
    # exactamente. Un recibo variable con solo dos cargos no es demostrable.
    if len(intervalos) < _MIN_INTERVALOS_SIN_CORROBORAR and len(importes_distintos) > 1:
        return None

    importe_actual = importes[-1] if precio_fijo else round(median(importes), 2)

    cambio_precio = None
    if precio_fijo and importes[0] != importes[-1]:
        cambio_precio = {
            "importe_anterior": importes[0],
            "importe_actual": importes[-1],
            "diferencia": round(importes[-1] - importes[0], 2),
        }

    dias_sin_cobrar = (hoy - fechas[-1]).days
    activa = dias_sin_cobrar <= dias_cadencia * _FACTOR_INACTIVIDAD

    categoria = movimientos[0]["categoria"]

    return {
        "comercio": comercio,
        "categoria": categoria,
        "tipo": "suscripcion" if categoria in _CATEGORIAS_SUSCRIPCION else "recibo",
        "cadencia": nombre_cadencia,
        "importe": importe_actual,
        "importe_variable": not precio_fijo,
        "coste_mensual_estimado": round(importe_actual / meses_cadencia, 2),
        "cargos": len(movimientos),
        "primer_cargo": fechas[0].isoformat(),
        "ultimo_cargo": fechas[-1].isoformat(),
        "activa": activa,
        "cambio_precio": cambio_precio,
    }


def analizar_recurrencia(movimientos: list[dict], hoy: date | None = None) -> dict:
    """
    Núcleo del análisis: recibe movimientos de gasto (importe < 0) ya
    filtrados y devuelve los pagos recurrentes.

    Está separado del acceso a la base de datos a propósito, para poder
    validarlo contra históricos sintéticos generados con distintas semillas
    (ver `tests/test_recurrencia.py`). Un umbral ajustado a un único conjunto
    de datos no demuestra nada.
    """
    hoy = hoy or date.today()

    por_comercio: dict[str, list[dict]] = {}
    for movimiento in movimientos:
        por_comercio.setdefault(movimiento["comercio"], []).append(movimiento)

    for movimientos_comercio in por_comercio.values():
        movimientos_comercio.sort(key=lambda m: m["fecha"])
    recurrentes = [
        analisis
        for comercio, movimientos in por_comercio.items()
        if (analisis := _analizar_comercio(comercio, movimientos, hoy)) is not None
    ]

    recurrentes.sort(key=lambda r: r["coste_mensual_estimado"], reverse=True)

    activos = [r for r in recurrentes if r["activa"]]
    total_mensual = round(sum(r["coste_mensual_estimado"] for r in activos), 2)

    # La respuesta se entrega ya resuelta, no en crudo. Con la lista plana y un
    # `cambio_precio: null` en siete de ocho entradas, el modelo agregaba mal y
    # contestaba "ninguna suscripción ha subido de precio. Sin embargo, Netflix
    # ha subido de 13,99 a 15,99 €": se contradecía en la misma frase. Separar
    # las listas y sacar las subidas a un campo propio le quita el trabajo de
    # agregar, que es donde un modelo de 8B falla, y no cuesta latencia.
    subidas = [
        {"comercio": r["comercio"], **r["cambio_precio"]}
        for r in recurrentes
        if r["cambio_precio"]
    ]

    return {
        "estado": "ok",
        "suscripciones": [r for r in recurrentes if r["tipo"] == "suscripcion"],
        "recibos_fijos": [r for r in recurrentes if r["tipo"] == "recibo"],
        "hay_subidas_de_precio": bool(subidas),
        "subidas_de_precio": subidas,
        "total_mensual_estimado": total_mensual,
        "total_anual_estimado": round(total_mensual * 12, 2),
    }


def detectar_pagos_recurrentes(meses_historico: int = 24) -> dict:
    """
    Detecta los pagos recurrentes del cliente en los últimos `meses_historico`.

    Devuelve un dict serializable con la lista de pagos detectados (ordenada de
    mayor a menor coste mensual) y los totales estimados.
    """
    conn = conexion_lectura()
    try:
        filas = conn.execute(
            """
            SELECT fecha, importe, categoria, comercio
            FROM movimientos
            WHERE importe < 0
              AND fecha >= date('now', ?)
            ORDER BY comercio, fecha
            """,
            (f"-{int(meses_historico)} months",),
        ).fetchall()
    finally:
        conn.close()

    resultado = analizar_recurrencia([dict(f) for f in filas])
    resultado["meses_analizados"] = meses_historico
    return resultado
