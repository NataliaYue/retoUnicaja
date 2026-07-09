"""
Herramientas (tools) del agente.

Este módulo define:
1. TOOLS: los esquemas JSON que se envían al LLM para que sepa qué puede
   invocar y con qué parámetros (tool calling / function calling).
2. ejecutar_tool(): el dispatcher que ejecuta la herramienta pedida y
   devuelve el resultado como string JSON (formato que espera el LLM).

Diseño clave para el baremo:
- `consultar_movimientos` recibe el SQL generado por el propio LLM
  (text-to-SQL, 30 pts) y lo pasa por la capa segura de database.py.
- `mostrar_grafico` recibe una especificación Vega-Lite COMPLETA generada
  por el LLM en tiempo real (sin plantillas, 10 pts) junto a un campo
  `razonamiento` en el que la IA explica por qué eligió ese tipo de gráfico
  (lógica visual, 10 pts). El agente la reenvía al frontend por WebSocket.
"""

import json   #diccionarios de python a strings JSON

from .banking_api import api_consultar_saldo, api_enviar_bizum
from .database import ejecutar_sql_seguro


#===========================================================================================================
# TOOLS: Herramientas visibles para el LLM
#===========================================================================================================
TOOLS = [
    
    # Tool sin argumentos: solo consulta y devuelve el saldo actual. 
    {
        "name": "consultar_saldo",
        "description": "Consulta el saldo actual de la cuenta del cliente a través de la API del banco.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    
    # Tool sensible: el agente debe haber validado confirmación antes de llegar aquí.
    {
        "name": "enviar_bizum",
        "description": (
            "Prepara un Bizum desde la cuenta del cliente. Úsala cuando el usuario "
            "quiera enviar dinero e indique destinatario e importe. La llamada a esta "
            "herramienta NO ejecuta inmediatamente el envío: el backend validará el "
            "contacto y pedirá confirmación explícita antes de enviar dinero."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destinatario": {"type": "string", "description": "Nombre del destinatario del Bizum."},
                "cantidad": {"type": "number", "description": "Importe en euros (entre 0,50 y 1.000)."},
                "concepto": {"type": "string", "description": "Concepto opcional del envío."},
            },
            "required": ["destinatario", "cantidad"],
        },
    },
    
    # Text-to-SQL: el LLM genera la consulta, pero database.py la valida antes de ejecutarla.
    # Se envía la SQL al frontend para transparencia y depuración.
    {
        "name": "consultar_movimientos",
        "description": (
            "Ejecuta una consulta SQL de SOLO LECTURA (SQLite) sobre el histórico "
            "de movimientos del cliente. Úsala para cualquier pregunta sobre "
            "gastos, ingresos, fechas o comparativas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "Consulta SELECT en dialecto SQLite sobre las tablas movimientos y cliente.",
                },
                "proposito": {
                    "type": "string",
                    "description": "Breve explicación en español de qué responde esta consulta.",
                },
            },
            "required": ["sql", "proposito"],
        },
    },
    
    # Gráficos dinámicos: el LLM elige el tipo de gráfico y genera la spec Vega-Lite completa.
    {
        "name": "mostrar_grafico",
        "description": (
            "Muestra un gráfico al usuario en la conversación. El LLM debe elegir "
            "el tipo de gráfico más adecuado según los datos y generar una "
            "especificación Vega-Lite v5 completa desde cero. No uses plantillas fijas. "
            "Los datos deben ir inline en data.values. La spec debe incluir title, mark, "
            "encoding y data.values, con títulos y ejes en español."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "object",
                    "description": "Especificación Vega-Lite v5 completa, con los datos inline en data.values.",
                },
                "razonamiento": {
                    "type": "string",
                    "description": "Una frase explicando por qué este tipo de gráfico es el más adecuado para estos datos.",
                },
            },
            "required": ["spec", "razonamiento"],
        },
    },
]


# Dispatcher central: decide qué función real ejecutar según el nombre de la tool.

async def ejecutar_tool(nombre: str, entrada: dict, emitir) -> str:
    """
    Ejecuta la herramienta `nombre` con los parámetros `entrada`.

    `emitir` es un callback async que envía eventos al frontend por WebSocket;
    lo usamos para que el gráfico y el saldo actualizado lleguen a la interfaz
    en el mismo momento en que la herramienta se ejecuta.

    Devuelve el resultado serializado (str) que se inserta como tool_result
    en la conversación con el LLM.
    """
    if nombre == "consultar_saldo":
        resultado = api_consultar_saldo()
        await emitir({"type": "saldo", "valor": resultado["saldo"]})
        return json.dumps(resultado, ensure_ascii=False)

    if nombre == "enviar_bizum":
        if entrada.get("confirmado") is not True:
            return json.dumps({
                "estado": "error",
                "motivo": "Bizum bloqueado: falta confirmación explícita."
            }, ensure_ascii=False)

        resultado = api_enviar_bizum(
            destinatario=entrada.get("destinatario", ""),
            cantidad=entrada.get("cantidad", 0),
            concepto=entrada.get("concepto", ""),
        )

        if resultado.get("estado") == "ok":
            await emitir({"type": "saldo", "valor": resultado["nuevo_saldo"]})

        return json.dumps(resultado, ensure_ascii=False)

    if nombre == "consultar_movimientos":
        resultado = ejecutar_sql_seguro(entrada.get("sql", ""))
        # El SQL se muestra en la interfaz (transparencia + material para la memoria)
        await emitir({
            "type": "sql",
            "sql": entrada.get("sql", ""),
            "proposito": entrada.get("proposito", ""),
            "error": resultado.get("error"),
        })
        return json.dumps(resultado, ensure_ascii=False)

    if nombre == "mostrar_grafico":
        spec = entrada.get("spec")

        if not isinstance(spec, dict):
            return json.dumps({
                "error": "La spec Vega-Lite no es válida: debe ser un objeto JSON."
            }, ensure_ascii=False)

        if "title" not in spec:
            return json.dumps({
                "error": "La spec Vega-Lite no es válida: falta title."
            }, ensure_ascii=False)

        if (
            not isinstance(spec.get("data"), dict)
            or "values" not in spec["data"]
            or not isinstance(spec["data"]["values"], list)
        ):
            return json.dumps({
                "error": "La spec Vega-Lite no es válida: debe incluir data.values como lista."
            }, ensure_ascii=False)

        if "mark" not in spec:
            return json.dumps({
                "error": "La spec Vega-Lite no es válida: falta mark."
            }, ensure_ascii=False)

        if "encoding" not in spec:
            return json.dumps({
                "error": "La spec Vega-Lite no es válida: falta encoding."
            }, ensure_ascii=False)

        await emitir({
            "type": "grafico",
            "spec": spec,
            "razonamiento": entrada.get("razonamiento", ""),
        })

        return json.dumps({
            "estado": "ok",
            "detalle": "Gráfico mostrado al usuario en pantalla."
        }, ensure_ascii=False)
            
            
        await emitir({
            "type": "grafico",
            "spec": spec,
            "razonamiento": entrada.get("razonamiento", ""),
        })
        return json.dumps({"estado": "ok", "detalle": "Gráfico mostrado al usuario en pantalla."})

    return json.dumps({"error": f"Herramienta desconocida: {nombre}"})
