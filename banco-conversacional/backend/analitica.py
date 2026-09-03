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

from calendar import monthrange
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


# ==============================================================================
# Proyección de gasto a fin de mes ("Analítica Predictiva" del enunciado)
# ==============================================================================
#
# La proyección obvia —gasto hasta hoy ÷ días transcurridos × días del mes— es
# inservible en la práctica, y está medido: el alquiler y los recibos caen en
# los primeros días del mes, así que a principios de mes el ritmo diario sale
# disparatado. Backtest sobre 23 meses completos del histórico, error medio:
#
#                      día 5    día 10   día 15   día 20
#   ingenua           174,5 %    73,3 %   46,1 %   25,9 %
#   fijos aparte       31,7 %    18,4 %   13,6 %   10,6 %
#   + media histórica   8,7 %     7,6 %    8,4 %    7,7 %   <- la que se usa
#
# Dos correcciones, cada una con su motivo:
#
# 1. Los pagos fijos NO se extrapolan. Se sabe lo que cuestan al mes (lo
#    calcula el detector de recurrencia), así que entran por su valor completo
#    se hayan cobrado ya o no. Extrapolarlos es lo que rompe la ingenua.
#
# 2. El gasto variable se MEZCLA con la media histórica, pesando el ritmo
#    observado por lo avanzado que va el mes. El día 3 tres días de compras no
#    dicen casi nada del mes entero; el día 25, sí. Sin esta mezcla el error a
#    principios de mes se triplica, que es justo cuando la proyección aporta.
MESES_PARA_LA_MEDIA = 12


# Palabras con las que el modelo pide "todo el gasto" creyendo que es una
# categoría. No lo son, y devolverle "no hay datos en «total»" le hacía
# responder que el cliente no había gastado nada en el mes. El modelo pedía
# algo sensato; lo frágil era la herramienta.
_SINONIMOS_DE_TODO = frozenset({
    "total", "todas", "todo", "todos", "general", "global", "gasto", "gastos",
})


def _normalizar_categoria(categoria: str | None):
    """
    Devuelve la categoría válida, None si significa "todas", o un dict de error
    con la lista de categorías reales para que el modelo se autocorrija (igual
    que se hace con los errores de SQL).
    """
    if not categoria:
        return None

    limpia = categoria.strip().lower()
    if limpia in _SINONIMOS_DE_TODO:
        return None

    conn = conexion_lectura()
    try:
        validas = [r[0] for r in conn.execute(
            "SELECT DISTINCT categoria FROM movimientos WHERE importe < 0 ORDER BY 1")]
    finally:
        conn.close()

    if limpia in validas:
        return limpia

    return {
        "estado": "categoria_desconocida",
        "categoria_pedida": categoria,
        "motivo": f"«{categoria}» no es una categoría de gasto.",
        "categorias_validas": validas,
    }


def _proyectar(fijos: float, variable_observado: float, media_variable: float,
               dia: int, dias_del_mes: int) -> float:
    """El estimador, en un solo sitio: lo usan la proyección y su autocontraste."""
    peso = dia / dias_del_mes
    ritmo = variable_observado / dia * dias_del_mes
    return fijos + peso * ritmo + (1 - peso) * media_variable


def proyectar_gasto_mes(categoria: str | None = None,
                        meses_historico: int = MESES_PARA_LA_MEDIA) -> dict:
    """
    Proyecta el gasto del mes en curso, entero o de una categoría concreta.

    Devuelve el resultado ya resuelto (proyección, margen, tendencia), no las
    piezas sueltas: agregar es justo lo que un modelo de 8B hace mal, y ya
    costó una respuesta que se contradecía a sí misma con las suscripciones.

    **La proyección viene con su margen de error medido.** El acierto depende
    muchísimo de la categoría, y dar una cifra seca para todas sería creíble y
    falso. Error medio del estimador sobre 23 meses del histórico, el día 5:

        alquiler, gimnasio, internet   0 %   (son pagos fijos: se saben)
        suscripciones                  4 %
        total del mes                  9 %
        supermercado, gasolina, luz  17-22 %
        restaurantes, ocio, ropa     34-47 %
        farmacia, agua               52-60 %
        bizum_enviado                105 %   (impredecible por naturaleza)

    Así que el margen no se inventa: se calcula proyectando cada mes pasado con
    este mismo estimador y midiendo cuánto se equivocó. `fiabilidad` traduce eso
    a algo que el asistente pueda decir en voz alta sin prometer de más.
    """
    hoy = date.today()
    dias_del_mes = monthrange(hoy.year, hoy.month)[1]
    mes_actual = hoy.strftime("%Y-%m")

    normalizada = _normalizar_categoria(categoria)
    if isinstance(normalizada, dict):    # categoría desconocida: se explica
        return normalizada
    categoria = normalizada

    recurrentes = detectar_pagos_recurrentes()
    pagos_fijos = recurrentes["suscripciones"] + recurrentes["recibos_fijos"]
    comercios_fijos = {p["comercio"] for p in pagos_fijos}

    conn = conexion_lectura()
    try:
        sql = """
            SELECT strftime('%Y-%m', fecha) AS mes,
                   CAST(strftime('%d', fecha) AS INTEGER) AS dia,
                   comercio, -importe AS gasto
            FROM movimientos
            WHERE importe < 0 AND fecha >= date('now', ?)
        """
        params: list = [f"-{int(meses_historico) + 1} months"]
        if categoria:
            sql += " AND categoria = ?"
            params.append(categoria)
        filas = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    if not filas:
        return {
            "estado": "sin_datos",
            "categoria": categoria,
            "motivo": f"No hay gastos registrados en «{categoria}»." if categoria
                      else "No hay gastos registrados.",
        }

    # Los pagos fijos de esta categoría entran por su coste mensual conocido,
    # se hayan cobrado ya o no. Extrapolarlos es lo que rompe la regla de tres.
    comercios_presentes = {f["comercio"] for f in filas}
    fijos_al_mes = sum(p["coste_mensual_estimado"] for p in pagos_fijos
                       if p["comercio"] in comercios_presentes)

    gasto_hasta_hoy = variable_hasta_hoy = 0.0
    variable_por_mes: dict[str, float] = {}
    variable_por_mes_hasta_dia: dict[str, float] = {}

    for f in filas:
        es_fijo = f["comercio"] in comercios_fijos
        if f["mes"] == mes_actual:
            gasto_hasta_hoy += f["gasto"]
            if not es_fijo:
                variable_hasta_hoy += f["gasto"]
        elif not es_fijo:
            variable_por_mes[f["mes"]] = variable_por_mes.get(f["mes"], 0.0) + f["gasto"]
            if f["dia"] <= hoy.day:
                variable_por_mes_hasta_dia[f["mes"]] = (
                    variable_por_mes_hasta_dia.get(f["mes"], 0.0) + f["gasto"])

    if len(variable_por_mes) < 3:
        # Sin gasto variable pero con pagos fijos, la categoría es enteramente
        # recurrente (alquiler, gimnasio, internet): no hay nada que estimar,
        # se sabe. Son precisamente las que el backtest da con 0 % de error, y
        # tratarlas como "sin histórico" sería tirar la mejor predicción que
        # tenemos.
        if fijos_al_mes > 0:
            return {
                "estado": "ok",
                "categoria": categoria or "todas",
                "gasto_hasta_hoy": round(gasto_hasta_hoy, 2),
                "proyeccion_fin_de_mes": round(fijos_al_mes, 2),
                "margen": 0.0,
                "fiabilidad": "alta",
                "media_meses_anteriores": round(fijos_al_mes, 2),
                "tendencia": "en linea",
                "nota": "Es un pago fijo: el importe se conoce, no se estima.",
            }

        return {
            "estado": "sin_historico",
            "categoria": categoria,
            "motivo": "No hay suficientes meses anteriores con los que comparar.",
        }

    media_variable = sum(variable_por_mes.values()) / len(variable_por_mes)
    proyeccion = _proyectar(fijos_al_mes, variable_hasta_hoy, media_variable,
                            hoy.day, dias_del_mes)

    # Autocontraste: se proyecta cada mes pasado con el mismo estimador y el
    # mismo día de corte, y se mide el error. Es el margen real del método
    # sobre ESTOS datos, no una barra de error inventada.
    errores = []
    for mes, real_variable in variable_por_mes.items():
        real = fijos_al_mes + real_variable
        if real <= 0:
            continue
        otros = [v for m, v in variable_por_mes.items() if m != mes]
        estimado = _proyectar(fijos_al_mes,
                              variable_por_mes_hasta_dia.get(mes, 0.0),
                              sum(otros) / len(otros) if otros else 0.0,
                              hoy.day, dias_del_mes)
        errores.append(abs(estimado - real) / real * 100)

    error_pct = round(median(errores), 1) if errores else 0.0
    margen = round(proyeccion * error_pct / 100, 2)

    if error_pct < 10:
        fiabilidad = "alta"
    elif error_pct < 25:
        fiabilidad = "media"
    else:
        fiabilidad = "baja"

    media_total = fijos_al_mes + media_variable
    desviacion = proyeccion - media_total
    desviacion_pct = (desviacion / media_total * 100) if media_total else 0.0

    # Una desviación por debajo del margen de error del propio método no es
    # señal de nada, y avisar de ella sería alarmismo.
    if abs(desviacion_pct) < max(5.0, error_pct):
        tendencia = "en linea"
    else:
        tendencia = "por encima" if desviacion > 0 else "por debajo"

    # Payload deliberadamente corto. La primera versión devolvía catorce campos
    # (día del mes, desviación en euros y en %, error típico, pagos fijos,
    # meses comparados...) y el modelo se perdía: llegó a decir "no has
    # realizado ningún gasto este mes" teniendo 890,34 € delante. Es la misma
    # lección que con las suscripciones: lo que se puede resolver aquí no se
    # le pide a un 8B. Todo lo que era diagnóstico interno se queda dentro.
    return {
        "estado": "ok",
        "categoria": categoria or "todas",
        "gasto_hasta_hoy": round(gasto_hasta_hoy, 2),
        "proyeccion_fin_de_mes": round(proyeccion, 2),
        "margen": margen,
        "fiabilidad": fiabilidad,
        "media_meses_anteriores": round(media_total, 2),
        "tendencia": tendencia,
    }
