"""
Motor visual: decide si un resultado se puede pintar y genera la spec.

El reparto de responsabilidades es deliberado y sale de medir, no de opinar:

- SI se pinta lo decide el BACKEND, de forma determinista. Medido sobre cuatro
  variantes del system prompt y tres vueltas de las 34 preguntas: la decisión
  del modelo resultó inestable ante CUALQUIER edición del prompt, aunque no
  hablara de gráficos (11 de 24 en dos variantes intermedias, 3 de 24 en la
  final, cambiando solo dos líneas de fechas). Cinco de las seis preguntas que
  deben acabar en gráfico no pintaban NUNCA, y la única que sí lo hacía era la
  que llevaba lenguaje visual explícito ("muéstrame la evolución").

- QUÉ gráfico y por qué lo sigue eligiendo el LLM, en una llamada aparte con
  prompt mínimo. Es lo que puntúa como "gráficos sin plantillas" y como lógica
  visual, y un modelo de 8B es mucho más fiable en una tarea única que
  decidiendo entre responder y encadenar una herramienta.

Los datos NO se le piden al modelo: ya los tenemos. Se le pide solo la parte
visual (title, mark, encoding) y el backend inyecta `data.values`. Eso quita
~700 tokens de generación por gráfico —el modelo ya no tiene que copiar las
cifras— y elimina de raíz el fallo de inventárselas.
"""

import json
import re


# Tope de filas que se envían a pintar. Por encima de esto un gráfico deja de
# leerse, y el tope de `database.py` (MAX_FILAS) ya acota lo que llega aquí.
MAX_PUNTOS = 40

# La muestra que ve el modelo para decidir la marca. No necesita los datos
# enteros para elegir entre barras y línea, y mandárselos solo gasta prefill.
FILAS_MUESTRA = 4

# El JSON que se pide es pequeño (title, mark, encoding, razonamiento): sin los
# datos dentro, con 400 tokens sobra y se acota la latencia de la llamada.
MAX_TOKENS_SPEC = 400


def _es_numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def es_graficable(resultado: dict) -> bool:
    """
    Decide SI hay algo que pintar. Nunca decide QUÉ.

    Dos formas dan gráfico:
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


def preparar_datos(resultado: dict) -> tuple[list[str], list[dict]]:
    """
    Convierte el resultado SQL en filas de Vega-Lite y devuelve (columnas, values).

    El caso de una sola fila con varias cifras se pivota de ancho a largo:
    Vega-Lite no sabe poner nombres de columna en un eje sin un `transform`
    con `fold`, así que `{este_mes: 2036, mes_pasado: 2399}` se convierte en
    dos filas `{serie, valor}`. Con eso el modelo solo tiene que elegir la
    marca, que es lo que se le da bien.
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


SYSTEM_SPEC = (
    "Eres un generador de especificaciones Vega-Lite v5. "
    "Respondes SIEMPRE con un único objeto JSON y nada más: sin explicaciones, "
    "sin texto alrededor y sin bloques de código."
)


def _prompt_usuario(pregunta: str, columnas: list[str], values: list[dict]) -> str:
    muestra = json.dumps(values[:FILAS_MUESTRA], ensure_ascii=False)
    return f"""El usuario preguntó: "{pregunta}"

Una consulta devolvió {len(values)} filas con las columnas {columnas}.
Muestra de los datos: {muestra}

Devuelve un objeto JSON con EXACTAMENTE estas cuatro claves:
- "title": título del gráfico, en español.
- "mark": la marca Vega-Lite. Elige la que mejor cuente estos datos:
    evolución temporal o tendencia -> "line"
    ranking o comparación por categoría -> "bar"
    reparto de un total entre pocas categorías -> "arc"
- "encoding": el encoding de Vega-Lite v5. Usa EXACTAMENTE los nombres de
  columna de arriba en los campos "field", con su "type" ("nominal",
  "quantitative" o "temporal") y un "title" en español por eje.
- "razonamiento": UNA frase explicando por qué esa marca es la mejor para
  estos datos concretos.

NO incluyas la clave "data": los datos los inserta el sistema.
Los importes están en euros."""


async def generar_spec(cliente, modelo, pregunta, resultado, *, extra_body=None):
    """
    Pide al LLM solo la parte visual y devuelve (spec_completa, razonamiento).

    Devuelve (None, "") si el modelo no produce algo pintable. Nunca lanza: un
    gráfico que falla no puede tumbar la respuesta, que es lo que el usuario
    de verdad está esperando.
    """
    columnas, values = preparar_datos(resultado)
    if not values:
        return None, ""

    try:
        respuesta = await cliente.chat.completions.create(
            model=modelo,
            max_tokens=MAX_TOKENS_SPEC,
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYSTEM_SPEC},
                {"role": "user", "content": _prompt_usuario(pregunta, columnas, values)},
            ],
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        contenido = respuesta.choices[0].message.content or ""
    except Exception:
        return None, ""

    parcial = _extraer_json(contenido)
    if not isinstance(parcial, dict):
        return None, ""

    # `mark` y `encoding` son obligatorios: sin ellos vega-embed no pinta nada
    # y el gráfico se perdería en silencio, que es el fallo que ya se midió.
    if not parcial.get("mark") or not isinstance(parcial.get("encoding"), dict):
        return None, ""

    spec = {
        "title": parcial.get("title") or "",
        "mark": parcial["mark"],
        "encoding": parcial["encoding"],
        "data": {"values": values},
    }

    return spec, str(parcial.get("razonamiento") or "")
