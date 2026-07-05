"""
El agente conversacional: el "motor de razonamiento" que pide el reto.

Arquitectura: bucle agéntico clásico con tool calling.

    mensaje usuario ─▶ LLM ─▶ ¿quiere usar herramientas?
                        ▲            │ sí: ejecutar + devolver resultados
                        └────────────┘
                              │ no: respuesta final ─▶ usuario

Decisiones que puntúan en el baremo:
- STREAMING token a token por WebSocket → el usuario ve la respuesta según se
  genera (Agilidad, 15 pts). La latencia percibida hasta el primer token es
  de décimas de segundo.
- HISTORIAL por conexión → el LLM ve toda la conversación, así funcionan las
  preguntas de seguimiento ("¿y esta semana?") y las confirmaciones de Bizum
  (Conversación, 15 pts).
- AUTOCORRECCIÓN de SQL → si la consulta falla, el error vuelve al LLM como
  tool_result y en la siguiente vuelta la corrige (Consultas, 30 pts).
"""

from anthropic import AsyncAnthropic

from .config import MAX_ITERACIONES_AGENTE, MAX_TOKENS, MODELO, system_prompt
from .tools import TOOLS, ejecutar_tool

client = AsyncAnthropic()  # lee ANTHROPIC_API_KEY del entorno


class Agente:
    """Una instancia por conexión WebSocket: mantiene el historial de la conversación."""

    def __init__(self, emitir):
        """
        `emitir` es un callback async que envía un dict JSON al frontend.
        Tipos de evento que emite el agente:
          inicio_respuesta | texto (delta) | sql | grafico | saldo |
          fin_respuesta (texto completo, para TTS) | error
        """
        self.emitir = emitir
        self.historial: list[dict] = []
        self.system = system_prompt()

    async def procesar(self, mensaje_usuario: str) -> None:
        self.historial.append({"role": "user", "content": mensaje_usuario})
        await self.emitir({"type": "inicio_respuesta"})

        texto_completo = ""

        try:
            for _ in range(MAX_ITERACIONES_AGENTE):
                # 1) Llamada al LLM con streaming
                async with client.messages.stream(
                    model=MODELO,
                    max_tokens=MAX_TOKENS,
                    system=self.system,
                    messages=self.historial,
                    tools=TOOLS,
                ) as stream:
                    async for evento in stream:
                        # Reenviamos cada fragmento de texto al frontend al instante
                        if (
                            evento.type == "content_block_delta"
                            and evento.delta.type == "text_delta"
                        ):
                            texto_completo += evento.delta.text
                            await self.emitir({"type": "texto", "delta": evento.delta.text})

                    respuesta = await stream.get_final_message()

                # 2) Guardamos la respuesta del asistente en el historial
                self.historial.append({"role": "assistant", "content": respuesta.content})

                # 3) ¿Ha pedido herramientas? Si no, hemos terminado.
                if respuesta.stop_reason != "tool_use":
                    break

                # 4) Ejecutar cada tool_use y devolver los resultados al LLM
                resultados = []
                for bloque in respuesta.content:
                    if bloque.type == "tool_use":
                        salida = await ejecutar_tool(bloque.name, bloque.input, self.emitir)
                        resultados.append({
                            "type": "tool_result",
                            "tool_use_id": bloque.id,
                            "content": salida,
                        })
                self.historial.append({"role": "user", "content": resultados})
                # ... y el bucle vuelve a llamar al LLM con los resultados.

        except Exception as e:  # noqa: BLE001 — cualquier fallo debe llegar a la UI
            await self.emitir({"type": "error", "detalle": str(e)})

        await self.emitir({"type": "fin_respuesta", "texto": texto_completo.strip()})
