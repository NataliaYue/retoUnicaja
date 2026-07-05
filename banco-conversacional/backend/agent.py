import os
import json
from openai import AsyncOpenAI

from .config import MAX_ITERACIONES_AGENTE, MAX_TOKENS, MODELO, system_prompt
from .tools import TOOLS, ejecutar_tool

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

client = AsyncOpenAI(
    base_url=PROVIDER_BASE_URLS.get(PROVIDER),
    api_key=os.getenv(f"{PROVIDER.upper()}_API_KEY") or os.getenv("OPENAI_API_KEY")
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


# ==============================================================================
# CLASE CLÁSICA DEL AGENTE (AHORA MULTI-PROVEEDOR)
# ==============================================================================
class Agente:
    """Una instancia por conexión WebSocket: mantiene el historial de la conversación."""

    def __init__(self, emitir):
        self.emitir = emitir
        self.historial: list[dict] = []
        self.system = system_prompt()

    async def procesar(self, mensaje_usuario: str) -> None:
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
                    
                    # Ejecutas tu lógica de negocio (SQL, Bizum, etc.)
                    salida = await ejecutar_tool(tc["name"], args, self.emitir)
                    
                    # Estructura obligatoria de respuesta de herramientas en OpenAI
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