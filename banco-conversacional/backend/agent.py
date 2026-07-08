import os
import json
import unicodedata
from difflib import get_close_matches
from openai import AsyncOpenAI

from .config import MAX_ITERACIONES_AGENTE, MAX_TOKENS, MODELO, system_prompt
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

PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()

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




def es_consulta_saldo(mensaje: str) -> bool:
    mensaje = mensaje.lower().strip()

    expresiones_saldo = [
        "saldo",
        "saldo actual",
        "saldo disponible",
        "dinero disponible",
        "cuánto dinero tengo",
        "cuanto dinero tengo",
        "cuánto me queda",
        "cuanto me queda",
        "cuánto tengo",
        "cuanto tengo",
        "cual es mi saldo actual"
    ]

    return any(expr in mensaje for expr in expresiones_saldo)

def formatear_euros(cantidad: float) -> str:
    texto = f"{cantidad:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".") + " €"


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
    nombre_lower = nombre.lower()

    # Coincidencia exacta ignorando mayúsculas/minúsculas.
    for contacto in contactos:
        if contacto.lower() == nombre_lower:
            return {"estado": "exacto", "contacto": contacto}

    # Coincidencia parcial: "María" -> "María López".
    parciales = [
        contacto for contacto in contactos
        if nombre_lower in contacto.lower()
    ]

    if len(parciales) == 1:
        return {"estado": "sugerencia", "contacto": parciales[0]}

    # Coincidencia aproximada: "María Lipiz" -> "María López".
    sugerencias = get_close_matches(nombre, contactos, n=1, cutoff=0.65)

    if sugerencias:
        return {"estado": "sugerencia", "contacto": sugerencias[0]}

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
        
        
    async def responder_directo(self, texto: str) -> None:
        await self.emitir({"type": "inicio_respuesta"})
        await self.emitir({"type": "texto", "delta": texto})
        await self.emitir({"type": "fin_respuesta", "texto": texto})

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
            self.correccion_contacto_pendiente = None
            await self.responder_directo("De acuerdo, no haré el Bizum.")
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
                texto = salida.get("motivo", "No se ha podido enviar el Bizum.")

            await self.emitir({"type": "texto", "delta": texto})
            await self.emitir({"type": "fin_respuesta", "texto": texto})
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
    
    
    
    
    
    async def procesar(self, mensaje_usuario: str) -> None:
        
        if await self.gestionar_correccion_contacto_pendiente(mensaje_usuario):
            return
        
        # Si hay un Bizum pendiente, este mensaje se interpreta como confirmación/cancelación.
        if await self.gestionar_bizum_pendiente(mensaje_usuario):
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

            await self.emitir({"type": "texto", "delta": texto})
            await self.emitir({"type": "fin_respuesta", "texto": texto})
            return
        
        
        # En el estándar OpenAI, el system prompt se suele pasar como primer mensaje
        if not self.historial:
            self.historial.append({"role": "system", "content": self.system})

        self.historial.append({"role": "user", "content": mensaje_usuario})
        await self.emitir({"type": "inicio_respuesta"})

        texto_completo = ""

        try:
            for _ in range(MAX_ITERACIONES_AGENTE):
                # 1) Llamada al LLM con streaming (Compatible con OpenAI/DeepSeek/Ollama/vLLM)
                stream = await client.chat.completions.create(
                    model=MODELO,
                    max_tokens=MAX_TOKENS,
                    messages=self.historial,
                    tools=OPENAI_TOOLS,
                    stream=True,
                )

                tool_calls_locales = {}
                
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # Capturar fragmentos de texto (Streaming)
                    if delta.content:
                        texto_completo += delta.content
                        await self.emitir({"type": "texto", "delta": delta.content})

                    # Capturar fragmentos de invocación de herramientas
                    if delta.tool_calls:
                        for tool_call in delta.tool_calls:
                            idx = tool_call.index
                            if idx not in tool_calls_locales:
                                tool_calls_locales[idx] = {
                                    "id": tool_call.id,
                                    "name": tool_call.function.name,
                                    "arguments": ""
                                }
                            if tool_call.function.arguments:
                                tool_calls_locales[idx]["arguments"] += tool_call.function.arguments

                # Reconstruir la estructura del mensaje del asistente para guardarla en el historial
                msg_asistente = {"role": "assistant", "content": texto_completo or None}
                if tool_calls_locales:
                    msg_asistente["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["arguments"]}
                        }
                        for tc in tool_calls_locales.values()
                    ]

                # 2) Guardamos la respuesta del asistente en el historial
                self.historial.append(msg_asistente)

                # 3) ¿Ha pedido herramientas? Si no, romper el bucle agéntico.
                if not tool_calls_locales:
                    break

                # 4) Ejecutar cada tool_call y devolver los resultados al modelo
                for tc in tool_calls_locales.values():
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}

                    # ----------------------------------------------------------
                    # Confirmación obligatoria de Bizum
                    # ----------------------------------------------------------
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

                            await self.emitir({"type": "texto", "delta": texto})
                            await self.emitir({"type": "fin_respuesta", "texto": texto})
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

                            await self.emitir({"type": "texto", "delta": texto})
                            await self.emitir({"type": "fin_respuesta", "texto": texto})
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

                        await self.emitir({"type": "texto", "delta": texto})
                        await self.emitir({"type": "fin_respuesta", "texto": texto})
                        return

                    # Resto de herramientas: saldo, movimientos, gráficos...
                    salida = await ejecutar_tool(tc["name"], args, self.emitir)

                    self.historial.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "content": str(salida),
                    })
                # El bucle continúa hacia la siguiente iteración enviando los resultados al LLM

        except Exception as e:
            await self.emitir({"type": "error", "detalle": str(e)})

        await self.emitir({"type": "fin_respuesta", "texto": texto_completo.strip()})