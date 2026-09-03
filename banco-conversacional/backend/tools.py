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

from .analitica import detectar_pagos_recurrentes, proyectar_gasto_mes
from .banking_api import (
    api_consultar_saldo,
    api_enviar_bizum,
    api_listar_contactos_bizum,
)
from .config import VISUALES_AL_LLM
from .database import ejecutar_sql_seguro


#===========================================================================================================
# TOOLS: Herramientas visibles para el LLM
#===========================================================================================================
TOOLS = [
    
    # Tool sin argumentos: solo consulta y devuelve el saldo actual. 
    {
        "name": "consultar_saldo",
        "description": (
            "Consulta el saldo actual de la cuenta DEL TITULAR con el que estás hablando, "
            "a través de la API del banco. Solo existe esa cuenta: no puedes consultar la de "
            "ninguna otra persona. Si te preguntan por el saldo de otro (por ejemplo, de un "
            "contacto de Bizum), NO llames a esta herramienta: no devolvería su saldo sino el "
            "del titular, y responderías con el dato de otra persona."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    
    # Tool sensible: el agente debe haber validado confirmación antes de llegar aquí.
    {
        "name": "enviar_bizum",
        "description": (
           "Prepara un Bizum desde la cuenta del cliente. Úsala INMEDIATAMENTE en cuanto "
            "el usuario mencione la intención de enviar dinero y un destinatario. "
            "NUNCA des instrucciones sobre la aplicación ni pidas confirmación verbal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destinatario": {"type": "string", "description": "Nombre del destinatario del Bizum."},
                "cantidad": {"type": "number", "description": "Importe en euros (entre 0,50 y 1.000)."},
                "concepto": {"type": "string", "description": "Concepto opcional del envío."},
            },
            "required": ["destinatario"],
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
    
    # Analítica avanzada: la recurrencia no está escrita en ninguna columna,
    # se infiere. Se resuelve en Python (determinista) en vez de pedirle al
    # LLM que la deduzca por SQL, que es donde un modelo de 8B se rompe.
    {
        "name": "analizar_suscripciones",
        "description": (
            "Detecta los pagos recurrentes del cliente: suscripciones (Netflix, Spotify...), "
            "cuotas (gimnasio) y recibos fijos (luz, agua, internet, alquiler). Úsala SIEMPRE "
            "que pregunte a qué está suscrito, qué pagos fijos o periódicos tiene, cuánto le "
            "cuestan al mes o al año, si alguno le ha subido de precio, o si alguno ha dejado "
            "de cobrarse. No intentes deducir esto con SQL: esta herramienta ya calcula la "
            "cadencia, el coste mensual equivalente y los cambios de precio."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },

    # Gráficos dinámicos: el LLM elige el tipo de gráfico y genera la spec Vega-Lite completa.
    #
    # El agente puede seguir llamándola por su cuenta, y cuando lo hace el gráfico
    # es decisión suya de principio a fin. Lo que ha cambiado es que ya no es la
    # ÚNICA vía: si termina el turno sin pintar y el resultado tenía algo que
    # pintar, el backend lanza una llamada dedicada (ver graficos.py). Está
    # medido que por sí solo no pinta en 5 de las 6 preguntas que deberían
    # acabar en gráfico, y que esa decisión se mueve con cualquier edición del
    # system prompt, aunque no hable de gráficos.
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
    
    # Analítica predictiva: el sistema es retrospectivo salvo por aquí.
    # La proyección no es una regla de tres: los pagos fijos entran por su
    # coste mensual conocido y el gasto variable se mezcla con la media
    # histórica. Pedirle esto al LLM por SQL no funcionaría.
    {
        "name": "proyectar_gasto",
        "description": (
            "Proyecta cuánto va a gastar el cliente al final del mes en curso, en "
            "total o en una categoría concreta, y lo compara con su media. Úsala "
            "cuando pregunte cuánto va a gastar este mes, cuánto gastará en algo, "
            "si va a gastar más o menos de lo normal, o si le pide una previsión. "
            "Devuelve la proyección ya calculada junto a su margen de error real: "
            "no la deduzcas tú ni la estimes con SQL, porque los pagos fijos del "
            "principio de mes distorsionan cualquier regla de tres."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "categoria": {
                    "type": "string",
                    "description": (
                        "Categoría a proyectar, si el usuario pregunta por una "
                        "concreta. Debe ser un valor exacto de la columna "
                        "`categoria`. Omítela para el gasto total del mes."
                    ),
                },
            },
            "required": [],
        },
    },

    # Tablas: la otra mitad de la Generative UI que pide el enunciado
    # ("gráficos y tablas"). Poder elegir ENTRE tabla y gráfico es lo que
    # convierte la lógica visual en una decisión de representación de verdad,
    # en vez de elegir entre cuatro marcas de Vega-Lite.
    {
        "name": "mostrar_tabla",
        "description": (
            "Muestra una tabla al usuario. Úsala cuando lo que importa son las "
            "cifras exactas, o cuando cada fila tiene varios atributos que se "
            "leen mejor alineados que dibujados (por ejemplo, la lista de pagos "
            "recurrentes con su cadencia y su coste mensual). Elige tú qué "
            "columnas merecen aparecer y en qué orden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título de la tabla, en español."},
                "columnas": {
                    "type": "array",
                    "description": (
                        "Columnas a mostrar. Cada una con 'campo' (nombre exacto del dato), "
                        "'titulo' (encabezado en español) y 'formato' "
                        "('euros', 'numero', 'fecha' o 'texto')."
                    ),
                    "items": {"type": "object"},
                },
                "filas": {
                    "type": "array",
                    "description": "Las filas de datos, como objetos con las claves de 'campo'.",
                    "items": {"type": "object"},
                },
                "razonamiento": {
                    "type": "string",
                    "description": "Una frase explicando por qué una tabla es lo mejor para estos datos.",
                },
            },
            "required": ["columnas", "filas", "razonamiento"],
        },
    },

    {
        "name": "listar_contactos_bizum",
        "description": (
            "Devuelve la lista exacta de contactos con los que el cliente ha operado "
            "por Bizum. Úsala SIEMPRE Y DIRECTAMENTE si el usuario pregunta cuáles "
            "son sus contactos, a quién puede enviar dinero, o si pide ver su agenda."
        ),
        "input_schema": {
            "type": "object", 
            "properties": {}, 
            "required": []
        },
    },
    
]


# Las herramientas visuales pueden ocultarse al LLM (VISUALES_AL_LLM=0). Siguen
# existiendo en el dispatcher: es el motor visual quien las invoca, en su llamada
# dedicada DESPUÉS de `fin_respuesta`. Cuando el modelo las llama él mismo lo hace
# dentro del bucle, o sea antes de emitir la respuesta, y ahí bloquea la voz.
TOOLS_VISUALES = ("mostrar_grafico", "mostrar_tabla")

if not VISUALES_AL_LLM:
    TOOLS = [t for t in TOOLS if t["name"] not in TOOLS_VISUALES]


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

    if nombre == "analizar_suscripciones":
        return json.dumps(detectar_pagos_recurrentes(), ensure_ascii=False)

    if nombre == "proyectar_gasto":
        categoria = (entrada.get("categoria") or "").strip() or None
        return json.dumps(proyectar_gasto_mes(categoria), ensure_ascii=False)

    if nombre == "mostrar_grafico":
        spec = entrada.get("spec")

        # Los modelos pequeños a veces serializan la spec como string JSON
        # en vez de como objeto: se intenta parsear antes de rechazarla.
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError as e:
                return json.dumps({
                    "error": f"La spec Vega-Lite llegó como string y no es JSON válido: {e}. Reintenta."
                }, ensure_ascii=False)

        if not isinstance(spec, dict):
            return json.dumps({
                "error": "La spec Vega-Lite no es válida: debe ser un objeto JSON."
            }, ensure_ascii=False)

        # Validación mínima: solo se exige que haya datos inline en algún
        # nivel. title/mark/encoding pueden vivir dentro de layer/hconcat/
        # vconcat, así que exigirlos en la raíz rechazaría specs válidas.
        # Si la spec tiene otros defectos, vega-embed lo notifica en la UI.
        if not _tiene_datos_inline(spec):
            return json.dumps({
                "error": "La spec Vega-Lite no incluye datos: añade data.values "
                         "(lista no vacía) con los datos reales de la consulta."
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
        
    if nombre == "mostrar_tabla":
        columnas = entrada.get("columnas")
        filas = entrada.get("filas")

        if not isinstance(columnas, list) or not columnas:
            return json.dumps({
                "error": "La tabla necesita 'columnas': una lista con campo, titulo y formato."
            }, ensure_ascii=False)

        if not isinstance(filas, list) or not filas:
            return json.dumps({
                "error": "La tabla no incluye datos: añade 'filas' con los datos reales."
            }, ensure_ascii=False)

        await emitir({
            "type": "tabla",
            "title": entrada.get("title", ""),
            "columnas": columnas,
            "filas": filas,
            "razonamiento": entrada.get("razonamiento", ""),
        })

        return json.dumps({
            "estado": "ok",
            "detalle": "Tabla mostrada al usuario en pantalla."
        }, ensure_ascii=False)

    if nombre == "listar_contactos_bizum":
            resultado = api_listar_contactos_bizum()
            return json.dumps(resultado, ensure_ascii=False)    

    return json.dumps({"error": f"Herramienta desconocida: {nombre}"})


def _tiene_datos_inline(spec: dict) -> bool:
    """True si la spec (o alguna de sus vistas anidadas) trae data.values no vacío."""
    data = spec.get("data")
    if isinstance(data, dict) and isinstance(data.get("values"), list) and data["values"]:
        return True

    for clave in ("layer", "hconcat", "vconcat", "concat"):
        hijos = spec.get(clave)
        if isinstance(hijos, list) and any(
            isinstance(h, dict) and _tiene_datos_inline(h) for h in hijos
        ):
            return True

    # facet / repeat envuelven la vista real en spec.spec
    if isinstance(spec.get("spec"), dict):
        return _tiene_datos_inline(spec["spec"])

    return False
