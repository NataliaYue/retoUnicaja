import os
import re
import json
import unicodedata
from difflib import get_close_matches
from openai import AsyncOpenAI

from .config import MAX_ITERACIONES_AGENTE, MAX_TOKENS, MODELO, TEMPERATURA, system_prompt
from .tools import TOOLS, ejecutar_tool
from .banking_api import api_listar_contactos_bizum

# ==============================================================================
# CONFIGURACIÓN DINÁMICA DEL PROVEEDOR
# ==============================================================================
PROVIDER_BASE_URLS = {
    "openai": None,  # Usa el oficial de OpenAI
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"),
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()

API_KEY = os.getenv(f"{PROVIDER.upper()}_API_KEY") or os.getenv("OPENAI_API_KEY")

# Ollama no necesita clave real, pero AsyncOpenAI exige algún valor.
if PROVIDER == "ollama" and not API_KEY:
    API_KEY = "ollama"

if not API_KEY:
    raise RuntimeError(
        f"No se ha encontrado API key para el proveedor '{PROVIDER}'. "
        f"Define {PROVIDER.upper()}_API_KEY o OPENAI_API_KEY en tu archivo .env."
    )

client = AsyncOpenAI(
    base_url=PROVIDER_BASE_URLS.get(PROVIDER),
    api_key=API_KEY,
)

# Adaptador por si tus TOOLS actuales siguen el formato input_schema de Anthropic
def adaptar_herramientas_a_openai(anthropic_tools):
    openai_tools = []
    for t in anthropic_tools:
        if "type" in t and t["type"] == "function":
            openai_tools.append(t)
            continue
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}})
            }
        })
    return openai_tools if openai_tools else None

OPENAI_TOOLS = adaptar_herramientas_a_openai(TOOLS)

# Qwen3 activa por defecto un modo "thinking" que multiplica la latencia y
# consume MAX_TOKENS antes de generar la respuesta. En Ollama se desactiva
# pasando reasoning_effort="none" por el endpoint OpenAI-compatible.
EXTRA_BODY = (
    {"reasoning_effort": "none"}
    if PROVIDER == "ollama" and "qwen3" in MODELO.lower()
    else None
)




def es_consulta_saldo(mensaje: str) -> bool:
    mensaje = normalizar_texto(mensaje)

    expresiones_saldo = [
    "saldo",
    "saldo actual",
    "saldo disponible",
    "dinero disponible",
    "cuanto dinero tengo",
    "cuanto me queda",
    "cual es mi saldo actual",
]

    return any(expr in mensaje for expr in expresiones_saldo)

def formatear_euros(cantidad: float) -> str:
    texto = f"{cantidad:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".") + " €"



def limpiar_markdown_respuesta(texto: str) -> str:
    """
    Elimina Markdown básico que algunos modelos locales añaden aunque el prompt diga que no.
    Pensado para respuestas habladas: importes sin **negrita**, sin cursiva y sin bloques.
    """
    if not texto:
        return texto

    # Quita bloques de código ```...```
    texto = texto.replace("```", "")

    # Quita negritas/cursivas alrededor de texto o importes.
    texto = texto.replace("**", "")
    texto = texto.replace("__", "")
    texto = texto.replace("*", "")
    texto = texto.replace("_", "")

    # Quita backticks inline.
    texto = texto.replace("`", "")

    # Arregla espacios raros.
    texto = re.sub(r"\s+", " ", texto).strip()

    return texto


def extraer_peticion_bizum(mensaje: str) -> dict | None:
    """
    Detecta peticiones de Bizum con distintos órdenes naturales.

    Ejemplos admitidos:
    - Haz un Bizum a María López de 20 euros
    - Envía 20 euros a María López por Bizum
    - Envía 20 € por Bizum a María López
    """
    texto = mensaje.strip()

    if "bizum" not in normalizar_texto(texto):
        return None

    patrones = [
        # Haz un Bizum a María López de 20 euros
        (
            r"^(?:haz|hacer|envia|envía|manda|mandar)\s+"
            r"(?:un\s+)?bizum\s+a\s+"
            r"(?P<destinatario>.+?)\s+(?:de|por)\s+"
            r"(?P<cantidad>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?$"
        ),

        # Envía 20 euros a María López por Bizum
        (
            r"^(?:envia|envía|manda|mandar)\s+"
            r"(?P<cantidad>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?\s+a\s+"
            r"(?P<destinatario>.+?)\s+(?:por\s+)?bizum$"
        ),

        # Envía 20 € por Bizum a María López
        (
            r"^(?:envia|envía|manda|mandar)\s+"
            r"(?P<cantidad>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?\s+"
            r"(?:por\s+)?bizum\s+a\s+"
            r"(?P<destinatario>.+?)$"
        ),
    ]

    for patron in patrones:
        match = re.search(patron, texto, flags=re.IGNORECASE)

        if not match:
            continue

        destinatario = match.group("destinatario").strip()
        cantidad_txt = match.group("cantidad").replace(",", ".")

        try:
            cantidad = round(float(cantidad_txt), 2)
        except ValueError:
            return None

        return {
            "destinatario": destinatario,
            "cantidad": cantidad,
            "concepto": "",
        }

    return None


def limpiar_razonamiento(texto: str) -> str:
    """
    Qwen3 puede emitir bloques <think>...</think> (vacíos con /no_think).
    Se eliminan para que no aparezcan en el chat ni en el TTS.
    """
    return re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL).strip()


def normalizar_texto(texto: str) -> str:
    """
    Normaliza texto para comparar respuestas cortas:
    - pasa a minúsculas
    - elimina espacios extra
    - elimina tildes y diacríticos
    """
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto


def es_confirmacion_bizum(mensaje: str) -> bool:
    mensaje = normalizar_texto(mensaje)

    
    return mensaje in {
        "si",
        "confirmo",
        "si confirmo",
        "confirmado",
        "adelante",
        "hazlo",
        "envialo",
        "mandalo",
        "ok",
        "vale",
        "de acuerdo",
        "correcto",
        "si, confirmo"
    }


def es_cancelacion_bizum(mensaje: str) -> bool:
    mensaje = normalizar_texto(mensaje)

    return mensaje in {
        "no",
        "cancela",
        "cancelar",
        "no confirmo",
        "mejor no",
        "dejalo",
        "olvidalo",
    }

def buscar_contacto_bizum(nombre: str) -> dict:
    """
    Valida el destinatario contra los contactos conocidos de Bizum.

    Devuelve:
    - {"estado": "exacto", "contacto": "..."}
    - {"estado": "sugerencia", "contacto": "..."}
    - {"estado": "no_encontrado"}
    """
    nombre = nombre.strip()

    if not nombre:
        return {"estado": "no_encontrado"}

    respuesta = api_listar_contactos_bizum()

    if respuesta.get("estado") != "ok":
        return {"estado": "no_encontrado"}

    contactos = respuesta["contactos"]
    nombre_normalizado = normalizar_texto(nombre)

    for contacto in contactos:
        if normalizar_texto(contacto) == nombre_normalizado:
            return {"estado": "exacto", "contacto": contacto}

    parciales = [
        contacto for contacto in contactos
        if nombre_normalizado in normalizar_texto(contacto)
    ]

    if len(parciales) == 1:
        return {"estado": "sugerencia", "contacto": parciales[0]}

    contactos_normalizados = {
        normalizar_texto(contacto): contacto
        for contacto in contactos
    }

    sugerencias = get_close_matches(
        nombre_normalizado,
        list(contactos_normalizados.keys()),
        n=1,
        cutoff=0.65,
    )

    if sugerencias:
        return {
            "estado": "sugerencia",
            "contacto": contactos_normalizados[sugerencias[0]],
        }

    return {"estado": "no_encontrado"}
# ==============================================================================
# CLASE CLÁSICA DEL AGENTE
# ==============================================================================
class Agente:
    """Una instancia por conexión WebSocket: mantiene el historial de la conversación."""

    def __init__(self, emitir):
        self.emitir = emitir
        self.historial: list[dict] = []
        self.system = system_prompt()
        # Guarda un Bizum pendiente hasta que el usuario confirme o cancele.
        self.bizum_pendiente: dict | None = None
        self.correccion_contacto_pendiente: dict | None = None
        
        
    def asegurar_system_en_historial(self) -> None:
        """Añade el system prompt una sola vez, antes del primer turno."""
        if not self.historial:
            self.historial.append({
                "role": "system",
                "content": self.system,
            })


    async def responder_directo(
        self,
        texto: str,
        *,
        emitir_inicio: bool = True,
    ) -> None:
        """
        Emite una respuesta gestionada por el backend y la registra
        como respuesta del asistente.
        """
        texto = limpiar_markdown_respuesta(texto)

        self.historial.append({
            "role": "assistant",
            "content": texto,
        })

        if emitir_inicio:
            await self.emitir({"type": "inicio_respuesta"})

        await self.emitir({
            "type": "texto",
            "delta": texto,
        })

        await self.emitir({
            "type": "fin_respuesta",
            "texto": texto,
        })
    async def gestionar_correccion_contacto_pendiente(self, mensaje_usuario: str) -> bool:
        if not self.correccion_contacto_pendiente:
            return False

        if es_confirmacion_bizum(mensaje_usuario):
            pendiente = self.correccion_contacto_pendiente
            self.correccion_contacto_pendiente = None

            contacto = pendiente["contacto_sugerido"]
            cantidad = pendiente["cantidad"]
            concepto = pendiente.get("concepto", "")

            self.bizum_pendiente = {
                "destinatario": contacto,
                "cantidad": cantidad,
                "concepto": concepto,
            }

            texto = (
                f"Perfecto. Vas a enviar {formatear_euros(cantidad)} "
                f"a {contacto}. ¿Confirmas el envío?"
            )

            await self.responder_directo(texto)
            return True

        if es_cancelacion_bizum(mensaje_usuario):
            pendiente = self.bizum_pendiente
            self.bizum_pendiente = None

            destinatario = pendiente["destinatario"]
            cantidad = pendiente["cantidad"]

            texto = (
                f"De acuerdo, cancelo el Bizum de {formatear_euros(cantidad)} "
                f"a {destinatario}. No se ha enviado dinero y el saldo no ha cambiado."
            )

            await self.responder_directo(texto)
            return True

        await self.responder_directo(
            "Necesito que me confirmes si ese es el contacto correcto. "
            "Responde “sí” para usarlo o “no” para cancelar."
        )
        return True
    
    async def gestionar_bizum_pendiente(self, mensaje_usuario: str) -> bool:
        """
        Si hay un Bizum pendiente, este método decide si el usuario
        confirma, cancela o responde de forma ambigua.

        Devuelve True si el mensaje ya se ha gestionado.
        Devuelve False si no había Bizum pendiente.
        """
        if not self.bizum_pendiente:
            return False

        if es_confirmacion_bizum(mensaje_usuario):
            pendiente = self.bizum_pendiente
            self.bizum_pendiente = None
            pendiente["confirmado"] = True

            await self.emitir({"type": "inicio_respuesta"})

            salida_json = await ejecutar_tool("enviar_bizum", pendiente, self.emitir)
            salida = json.loads(salida_json)

            if salida.get("estado") == "ok":
                texto = (
                    f"Bizum enviado correctamente a {salida['destinatario']} "
                    f"por {formatear_euros(salida['cantidad'])}. "
                    f"Tu nuevo saldo es {formatear_euros(salida['nuevo_saldo'])}."
                )
            else:
                texto = (
                    salida.get("motivo")
                    or salida.get("error")
                    or f"No se ha podido enviar el Bizum. Respuesta interna: {salida}"
                )

            await self.responder_directo(
                texto,
                emitir_inicio=False,
            )
            return True

        if es_cancelacion_bizum(mensaje_usuario):
            self.bizum_pendiente = None
            await self.responder_directo("De acuerdo, cancelo el Bizum.")
            return True

        texto = (
            "Tengo un Bizum pendiente. Respóndeme con una confirmación clara, "
            "por ejemplo “sí, confirmo”, o dime “no” para cancelarlo."
        )
        await self.responder_directo(texto)
        return True
    
    
    async def preparar_bizum_desde_backend(self, datos: dict) -> None:
        destinatario_original = datos.get("destinatario", "").strip()
        cantidad = round(float(datos.get("cantidad", 0)), 2)
        concepto = datos.get("concepto", "").strip()

        validacion = buscar_contacto_bizum(destinatario_original)

        if validacion["estado"] == "no_encontrado":
            texto = (
                f"No encuentro a “{destinatario_original}” como contacto de Bizum. "
                "Revisa el nombre o usa un contacto con el que ya hayas hecho Bizum."
            )
            await self.responder_directo(texto)
            return

        if validacion["estado"] == "sugerencia":
            contacto_sugerido = validacion["contacto"]

            self.correccion_contacto_pendiente = {
                "destinatario_original": destinatario_original,
                "contacto_sugerido": contacto_sugerido,
                "cantidad": cantidad,
                "concepto": concepto,
            }

            texto = (
                f"No encuentro exactamente “{destinatario_original}”. "
                f"¿Querías decir {contacto_sugerido}?"
            )

            await self.responder_directo(texto)
            return

        destinatario = validacion["contacto"]

        self.bizum_pendiente = {
            "destinatario": destinatario,
            "cantidad": cantidad,
            "concepto": concepto,
        }

        texto = (
            f"Vas a enviar {formatear_euros(cantidad)} "
            f"a {destinatario}. ¿Confirmas el envío?"
        )

        await self.responder_directo(texto)
    
    
    async def procesar(self, mensaje_usuario: str) -> None:
        # Todos los mensajes entran en el historial,
        # también los gestionados directamente por el backend.
        self.asegurar_system_en_historial()
        self.historial.append({
            "role": "user",
            "content": mensaje_usuario,
        })
    
        if await self.gestionar_correccion_contacto_pendiente(mensaje_usuario):
            return
        
        # Si hay un Bizum pendiente, este mensaje se interpreta como confirmación/cancelación.
        if await self.gestionar_bizum_pendiente(mensaje_usuario):
            return
        peticion_bizum = extraer_peticion_bizum(mensaje_usuario)
        
        if peticion_bizum:
            await self.preparar_bizum_desde_backend(peticion_bizum)
            return

        # Atajo rápido: consultar saldo no necesita pasar por el LLM.
        if es_consulta_saldo(mensaje_usuario):
            await self.emitir({"type": "inicio_respuesta"})

            salida_json = await ejecutar_tool("consultar_saldo", {}, self.emitir)
            salida = json.loads(salida_json)

            if salida.get("estado") == "ok":
                texto = f"Tu saldo disponible es {formatear_euros(salida['saldo'])}."
            else:
                texto = "No he podido consultar tu saldo ahora mismo."

            await self.responder_directo(
                texto,
                emitir_inicio=False,
            )
            return
        
        

        await self.emitir({"type": "inicio_respuesta"})

        texto_final = ""
        reintento_vacio = False

        try:
            for _ in range(MAX_ITERACIONES_AGENTE):
                stream = await client.chat.completions.create(
                    model=MODELO,
                    max_tokens=MAX_TOKENS,
                    messages=self.historial,
                    tools=OPENAI_TOOLS,
                    stream=True,
                    temperature=TEMPERATURA,
                    extra_body=EXTRA_BODY,
                )

                tool_calls_locales = {}
                texto_iteracion = ""

                async for chunk in stream:
                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta

                    # Importante:
                    # NO emitimos texto todavía. Lo guardamos en buffer.
                    # Solo se mostrará si al final no hay tool calls.
                    if delta.content:
                        texto_iteracion += delta.content

                    if delta.tool_calls:
                        for tool_call in delta.tool_calls:
                            idx = tool_call.index

                            if idx not in tool_calls_locales:
                                tool_calls_locales[idx] = {
                                    "id": tool_call.id,
                                    "name": tool_call.function.name,
                                    "arguments": "",
                                }

                            if tool_call.function.arguments:
                                tool_calls_locales[idx]["arguments"] += tool_call.function.arguments

                # Si no hay herramientas, ahora sí mostramos el texto del LLM.
                if not tool_calls_locales:
                    texto_iteracion = limpiar_razonamiento(texto_iteracion)
                    texto_iteracion = limpiar_markdown_respuesta(texto_iteracion)

                    # A veces qwen3 devuelve una iteración vacía (sin texto ni
                    # tools), sobre todo tras un tool_result. No se guarda en el
                    # historial: un content nulo hace que Ollama rechace TODAS
                    # las peticiones siguientes con 400 "invalid message content
                    # type: <nil>". Se reintenta una vez; si persiste, fallback.
                    if not texto_iteracion.strip():
                        if not reintento_vacio:
                            reintento_vacio = True
                            continue
                        texto_iteracion = (
                            "Perdona, no he podido redactar la respuesta. "
                            "¿Puedes repetir la pregunta?"
                        )

                    self.historial.append({"role": "assistant", "content": texto_iteracion})
                    texto_final += texto_iteracion
                    await self.emitir({"type": "texto", "delta": texto_iteracion})
                    break

                self.historial.append({
                    "role": "assistant",
                    # nunca None: Ollama rechaza mensajes con content nulo
                    "content": texto_iteracion,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in tool_calls_locales.values()
                    ],
                })

                # Si hay herramientas, NO mostramos texto_iteracion.
                # Responderán las tools o el backend controlado.
                for tc in tool_calls_locales.values():
                    try:
                        args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError as e:
                        # JSON corrupto (p.ej. spec truncada): se devuelve el error
                        # al modelo como tool_result para que se autocorrija,
                        # igual que se hace con los errores de SQL.
                        self.historial.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                            "content": json.dumps({
                                "error": f"Los argumentos no son JSON válido: {e}. Reintenta la llamada."
                            }, ensure_ascii=False),
                        })
                        continue

                    if tc["name"] == "enviar_bizum":
                        destinatario = args.get("destinatario", "").strip()
                        cantidad = round(float(args.get("cantidad", 0)), 2)
                        concepto = args.get("concepto", "").strip()

                        validacion = buscar_contacto_bizum(destinatario)

                        if validacion["estado"] == "no_encontrado":
                            texto = (
                                f"No encuentro a “{destinatario}” como contacto de Bizum. "
                                "Revisa el nombre o usa un contacto con el que ya hayas hecho Bizum."
                            )

                            # Toda llamada de herramienta debe tener su resultado
                            # antes de añadir una respuesta normal del asistente.
                            self.historial.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "name": tc["name"],
                                "content": json.dumps({
                                    "estado": "no_encontrado",
                                    "destinatario": destinatario,
                                }, ensure_ascii=False),
                            })

                            await self.responder_directo(
                                texto,
                                emitir_inicio=False,
                            )
                            return

                        if validacion["estado"] == "sugerencia":
                            contacto_sugerido = validacion["contacto"]

                            self.correccion_contacto_pendiente = {
                                "destinatario_original": destinatario,
                                "contacto_sugerido": contacto_sugerido,
                                "cantidad": cantidad,
                                "concepto": concepto,
                            }

                            texto = (
                                f"No encuentro exactamente “{destinatario}”. "
                                f"¿Querías decir {contacto_sugerido}?"
                            )

                            self.historial.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "name": tc["name"],
                                "content": json.dumps({
                                    "estado": "sugerencia_contacto",
                                    "destinatario_original": destinatario,
                                    "contacto_sugerido": contacto_sugerido,
                                    "cantidad": cantidad,
                                    "concepto": concepto,
                                }, ensure_ascii=False),
                            })

                            await self.responder_directo(
                                texto,
                                emitir_inicio=False,
                            )
                            return

                        destinatario = validacion["contacto"]

                        self.bizum_pendiente = {
                            "destinatario": destinatario,
                            "cantidad": cantidad,
                            "concepto": concepto,
                        }

                        texto = (
                            f"Vas a enviar {formatear_euros(cantidad)} "
                            f"a {destinatario}. ¿Confirmas el envío?"
                        )

                        self.historial.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                            "content": json.dumps({
                                "estado": "pendiente_confirmacion",
                                "destinatario": destinatario,
                                "cantidad": cantidad,
                                "concepto": concepto,
                            }, ensure_ascii=False),
                        })

                        await self.responder_directo(
                            texto,
                            emitir_inicio=False,
                        )
                        return

                    salida = await ejecutar_tool(tc["name"], args, self.emitir)

                    self.historial.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "content": str(salida),
                    })

        except Exception as e:
            await self.emitir({"type": "error", "detalle": str(e)})

        await self.emitir({"type": "fin_respuesta", "texto": texto_final.strip()})