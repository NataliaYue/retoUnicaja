"""
Motor visual: decide si un resultado merece refuerzo visual y cuál.

El reparto de responsabilidades es deliberado y sale de medir:

- SI se muestra algo lo decide el BACKEND, de forma determinista. Medido sobre
  cuatro variantes del system prompt y tres vueltas de las 34 preguntas: la
  decisión del modelo resultó inestable ante CUALQUIER edición del prompt,
  aunque no hablara de gráficos (11 de 24 en dos variantes intermedias, 3 de 24
  en la final, cambiando solo dos líneas de fechas). Cinco de las seis preguntas
  que debían acabar en gráfico no pintaban NUNCA.

- QUÉ representación —tabla o gráfico—, con qué columnas o qué marca, y POR QUÉ,
  lo sigue eligiendo el LLM en una llamada aparte con prompt mínimo. Eso es lo
  que puntúa como lógica visual y como "gráficos sin plantillas", y elegir entre
  tabla y gráfico es una decisión de representación de verdad, no elegir entre
  cuatro marcas de Vega-Lite.

Los datos NO se le piden al modelo: ya los tenemos. Se le pide solo la parte
visual y el backend inyecta las filas. Eso quita ~700 tokens de generación por
visual —el modelo ya no tiene que copiar las cifras— y elimina de raíz el fallo
de inventárselas.
"""

import json
import re


# Tope de filas que se envían a representar. Por encima de esto un gráfico deja
# de leerse y una tabla deja de caber; el tope de `database.py` (MAX_FILAS) ya
# acota lo que llega hasta aquí.
MAX_PUNTOS = 40

# La muestra que ve el modelo para decidir. No necesita los datos enteros para
# elegir entre tabla y barras, y mandárselos solo gasta prefill.
FILAS_MUESTRA = 3

# El JSON que se pide es pequeño (sin los datos dentro), así que con esto sobra
# y de paso se acota la latencia de la llamada.
MAX_TOKENS_VISUAL = 500


def _es_numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


# ──────────────────────────────────────────────────────────────────────────
# Gate: ¿hay algo que representar?
# ──────────────────────────────────────────────────────────────────────────

def es_graficable(resultado: dict) -> bool:
    """
    Decide SI hay algo que representar en un resultado SQL. Nunca decide QUÉ.

    Dos formas dan visual:
    - Varias filas con alguna columna numérica: serie temporal o ranking.
    - UNA fila con dos o más cifras: una comparación. Esta es la que el prompt
      nunca alcanzaba — "¿cuánto he gastado este mes comparado con el pasado?"
      devuelve una sola fila con dos columnas, y el criterio "varios valores
      comparables" no se le aplicaba nunca.
    """
    if resultado.get("error"):
        return False

    filas = resultado.get("filas") or []
    if not filas:
        return False

    numericas = [i for i, v in enumerate(filas[0]) if _es_numero(v)]
    if not numericas:
        return False

    if len(filas) >= 2:
        return True

    return len(numericas) >= 2


# ──────────────────────────────────────────────────────────────────────────
# Normalización: de la salida de cada tool a (columnas, filas)
# ──────────────────────────────────────────────────────────────────────────

def datos_de_sql(resultado: dict) -> tuple[list[str], list[dict]]:
    """
    Convierte el resultado SQL en filas de objetos.

    El caso de una sola fila con varias cifras se pivota de ancho a largo:
    Vega-Lite no sabe poner nombres de columna en un eje sin un `transform`
    con `fold`, así que `{este_mes: 2036, mes_pasado: 2399}` se convierte en
    dos filas `{serie, valor}`. Con eso el modelo solo tiene que elegir la
    representación, que es lo que se le da bien.
    """
    columnas = resultado.get("columnas") or []
    filas = resultado.get("filas") or []

    if len(filas) == 1:
        numericas = [i for i, v in enumerate(filas[0]) if _es_numero(v)]
        if len(numericas) >= 2:
            return ["serie", "valor"], [
                {"serie": columnas[i], "valor": filas[0][i]} for i in numericas
            ]

    return columnas, [dict(zip(columnas, fila)) for fila in filas[:MAX_PUNTOS]]


# Campos de `analizar_suscripciones` que valen para representar. Se dejan fuera
# los de diagnóstico interno (importe_variable, primer_cargo, cambio_precio):
# el modelo no los necesita para elegir columnas y solo gastan prefill.
CAMPOS_SUSCRIPCION = (
    "comercio", "tipo", "categoria", "cadencia",
    "importe", "coste_mensual_estimado", "cargos", "activa",
)


def datos_de_suscripciones(resultado: dict) -> tuple[list[str], list[dict]]:
    """
    Aplana suscripciones + recibos fijos en filas comparables.

    Es el mejor caso de tabla de toda la aplicación: ocho pagos con cadencia,
    importe y coste mensual equivalente. Leído en voz alta se convierte en la
    enumeración que ya estaba anotada como fallo de estilo ("estás suscrito a
    el gimnasio, Netflix, alquiler, luz…"); en una tabla se lee de un vistazo.
    """
    filas = []
    for clave in ("suscripciones", "recibos_fijos"):
        for item in resultado.get(clave) or []:
            filas.append({c: item.get(c) for c in CAMPOS_SUSCRIPCION})

    if not filas:
        return [], []

    return list(CAMPOS_SUSCRIPCION), filas[:MAX_PUNTOS]


def normalizar(nombre_tool: str, resultado: dict) -> tuple[list[str], list[dict]]:
    """Devuelve (columnas, filas) para la tool de origen, o ([], []) si no aplica."""
    if nombre_tool == "consultar_movimientos":
        if not es_graficable(resultado):
            return [], []
        return datos_de_sql(resultado)

    if nombre_tool == "analizar_suscripciones":
        return datos_de_suscripciones(resultado)

    return [], []


# ──────────────────────────────────────────────────────────────────────────
# La llamada dedicada
# ──────────────────────────────────────────────────────────────────────────

def _extraer_json(texto: str) -> dict | None:
    """
    Rescata el objeto JSON de la respuesta del modelo.

    Aunque se pida JSON puro, qwen3 puede envolverlo en ```json, colar un
    bloque <think> vacío delante o añadir una frase. Se limpia lo conocido y,
    si aun así no parsea, se recorta desde la primera llave hasta la última.
    """
    if not texto:
        return None

    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL)
    texto = re.sub(r"```(?:json)?", "", texto).strip()

    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin <= inicio:
        return None

    try:
        return json.loads(texto[inicio:fin + 1])
    except json.JSONDecodeError:
        return None


SYSTEM_VISUAL = (
    "Eres un diseñador de interfaces de datos. Eliges cómo representar un "
    "resultado y respondes SIEMPRE con un único objeto JSON y nada más: sin "
    "explicaciones, sin texto alrededor y sin bloques de código."
)


def _prompt_usuario(pregunta: str, columnas: list[str], filas: list[dict]) -> str:
    muestra = json.dumps(filas[:FILAS_MUESTRA], ensure_ascii=False)
    return f"""El usuario preguntó: "{pregunta}"

El resultado tiene {len(filas)} filas con las columnas {columnas}.
Muestra: {muestra}

Elige CÓMO representarlo y devuelve un objeto JSON.

Elige "grafico" cuando lo que importa es la forma: una tendencia, una
comparación de magnitudes o un reparto. Elige "tabla" cuando lo que importa
son las cifras exactas o cuando cada fila tiene varios atributos que se leen
mejor alineados que dibujados.

Si eliges GRÁFICO, devuelve estas claves:
  "representacion": "grafico"
  "title": título en español
  "mark": la marca Vega-Lite — "line" para evolución o tendencia, "bar" para
          ranking o comparación por categoría, "arc" para reparto de un total
  "encoding": encoding de Vega-Lite v5, usando EXACTAMENTE los nombres de
          columna de arriba en los "field", con su "type" ("nominal",
          "quantitative" o "temporal") y un "title" en español por eje
  "razonamiento": UNA frase explicando por qué esa representación

Si eliges TABLA, devuelve estas claves:
  "representacion": "tabla"
  "title": título en español
  "columnas": lista de objetos con "campo" (uno de los nombres de arriba,
          EXACTO), "titulo" (encabezado en español) y "formato", que debe ser
          "euros" para importes, "numero" para cantidades, "fecha" para fechas
          y "texto" para el resto. Incluye solo las columnas que aporten algo
          y ordénalas como se leerían mejor.
  "razonamiento": UNA frase explicando por qué esa representación

NO incluyas los datos: los inserta el sistema. Los importes están en euros."""


def _construir_tabla(parcial: dict, columnas: list[str], filas: list[dict]) -> dict | None:
    """Valida las columnas elegidas y arma la tabla con las filas reales."""
    elegidas = parcial.get("columnas")
    if not isinstance(elegidas, list) or not elegidas:
        return None

    FORMATOS = {"euros", "numero", "fecha", "texto"}
    limpias = []
    for col in elegidas:
        if not isinstance(col, dict):
            continue
        campo = col.get("campo")
        # Un campo inventado dejaría una columna entera vacía en pantalla.
        if campo not in columnas:
            continue
        formato = col.get("formato")
        limpias.append({
            "campo": campo,
            "titulo": str(col.get("titulo") or campo),
            "formato": formato if formato in FORMATOS else "texto",
        })

    if not limpias:
        return None

    campos = [c["campo"] for c in limpias]
    return {
        "title": parcial.get("title") or "",
        "columnas": limpias,
        "filas": [{c: fila.get(c) for c in campos} for fila in filas],
    }


def _construir_grafico(parcial: dict, filas: list[dict]) -> dict | None:
    """Valida la spec y le inyecta los datos reales."""
    # `mark` y `encoding` son obligatorios: sin ellos vega-embed no pinta nada
    # y el gráfico se perdería en silencio, que es el fallo que ya se midió.
    if not parcial.get("mark") or not isinstance(parcial.get("encoding"), dict):
        return None

    return {
        "title": parcial.get("title") or "",
        "mark": parcial["mark"],
        "encoding": parcial["encoding"],
        "data": {"values": filas},
    }


async def generar_visual(cliente, modelo, pregunta, columnas, filas, *, extra_body=None):
    """
    Pide al LLM la representación y devuelve (tipo, payload, razonamiento).

    `tipo` es "grafico", "tabla" o None. Nunca lanza: un refuerzo visual que
    falla no puede tumbar la respuesta, que es lo que el usuario está esperando.
    """
    if not filas or not columnas:
        return None, None, ""

    try:
        respuesta = await cliente.chat.completions.create(
            model=modelo,
            max_tokens=MAX_TOKENS_VISUAL,
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYSTEM_VISUAL},
                {"role": "user", "content": _prompt_usuario(pregunta, columnas, filas)},
            ],
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        contenido = respuesta.choices[0].message.content or ""
    except Exception:
        return None, None, ""

    parcial = _extraer_json(contenido)
    if not isinstance(parcial, dict):
        return None, None, ""

    razonamiento = str(parcial.get("razonamiento") or "")

    if parcial.get("representacion") == "tabla":
        tabla = _construir_tabla(parcial, columnas, filas)
        return ("tabla", tabla, razonamiento) if tabla else (None, None, "")

    grafico = _construir_grafico(parcial, filas)
    return ("grafico", grafico, razonamiento) if grafico else (None, None, "")
