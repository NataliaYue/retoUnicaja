import asyncio
import os
import re
import json
import traceback
import unicodedata
from difflib import get_close_matches
from openai import APIConnectionError, AsyncOpenAI

from .config import (
    BIZUM_MAX,
    BIZUM_MIN,
    INTENTOS_PIN,
    MAX_CHARS_TOOL_RESULT,
    MAX_ITERACIONES_AGENTE,
    MAX_MENSAJES_HISTORIAL,
    MAX_TOKENS,
    MODELO,
    PIN_BIZUM,
    TEMPERATURA,
    system_prompt,
)
from .tools import TOOLS, ejecutar_tool
from .banking_api import api_listar_contactos_bizum
from .graficos import generar_visual, normalizar

# ==============================================================================
# CONFIGURACIÓN DINÁMICA DEL PROVEEDOR
# ==============================================================================
PROVIDER_BASE_URLS = {
    "openai": None,  # Usa el oficial de OpenAI
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"),
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
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

# Las tools se declaran en formato Anthropic (name/description/input_schema) y
# el endpoint de Ollama las espera en formato OpenAI (function/parameters).
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
# pasando reasoning_effort="none".
EXTRA_BODY = (
    {"reasoning_effort": "none"}
    if PROVIDER == "ollama" and "qwen3" in MODELO.lower()
    else None
)


# Expresiones que identifican una pregunta por el saldo.
_DISPARADORES_SALDO = (
    "saldo",
    "cuanto dinero",
    "dinero disponible",
    "cuanto me queda",
    "cuanto tengo",
)

# Palabras que pueden acompañar a una pregunta de saldo sin cambiar lo que
# pide. Cualquier término fuera de esta lista significa que la pregunta no es 
# "¿cuánto tengo ahora?" y debe ir al LLM.
_RELLENO_SALDO = frozenset({
    "a", "actual", "actualmente", "ahora", "banco", "corriente", "cual",
    "cuanta", "cuanto", "cuenta", "dame", "de", "del", "dime", "dinero",
    "disponible", "disponibles", "el", "en", "es", "esta", "favor", "hay",
    "hola", "la", "las", "los", "me", "mi", "mis", "mismo", "mostrar",
    "muestra", "muestrame", "oye", "podrias", "por", "puedes", "que", "queda",
    "quedan", "quiero", "saber", "saldo", "tengo", "tiene", "ver", "y",
})


def es_consulta_saldo(mensaje: str) -> bool:
    """
    Atajo: las preguntas por el saldo actual se resuelven con una llamada a la
    API bancaria, sin pasar por el LLM.
    """
    texto = normalizar_texto(mensaje)
    texto = re.sub(r"[^\w\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    if not texto:
        return False

    if not any(disparador in texto for disparador in _DISPARADORES_SALDO):
        return False

    return all(palabra in _RELLENO_SALDO for palabra in texto.split())

def formatear_euros(cantidad: float) -> str:
    texto = f"{cantidad:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def validar_importe_bizum(cantidad: float) -> str | None:
    """
    Devuelve el motivo por el que este importe no se puede enviar, o None si
    es válido.

    Aplica los mismos límites que `api_enviar_bizum`, pero antes de dejar el
    Bizum pendiente.

    La API sigue comprobándolo por su cuenta: es la última palabra antes de
    mover dinero y no debe fiarse de que quien la llama haya validado nada.
    """
    if cantidad < BIZUM_MIN:
        return (
            f"El importe mínimo de un Bizum es {formatear_euros(BIZUM_MIN)}, "
            f"así que no puedo enviar {formatear_euros(cantidad)}."
        )

    if cantidad > BIZUM_MAX:
        return (
            f"El importe máximo de un Bizum es {formatear_euros(BIZUM_MAX)}, "
            f"así que no puedo enviar {formatear_euros(cantidad)}."
        )

    return None


def frases_emitibles(buffer: str, ya_emitido: int, *, final: bool = False) -> tuple[str, int]:
    """
    Del texto acumulado, devuelve el trozo listo para mostrar y la nueva marca.

    Se emite **por frases completas**, no por tokens, por tres razones:
    - Es la unidad que el TTS puede leer sin cortar a mitad de palabra.
    - Limita el parpadeo si la iteración acaba en un tool call y hay que
      descartar lo mostrado: se descarta una frase, no media palabra.
    - `limpiar_markdown_respuesta` trabaja sobre texto, no sobre tokens: un
      `**` puede llegar partido entre dos deltas.

    Dos cosas que hay que respetar y que no son obvias:
    - Si hay un `<think>` sin cerrar, no se emite nada todavía: el bloque se
      limpia entero, y soltarlo a medias lo dejaría a la vista del usuario.
    - El final de frase exige un espacio detrás. Sin eso, el punto de los
      millares en "1.234,56 €" se toma por un final de frase y se emite
      "Has gastado 1." como si fuera una respuesta completa.
    """
    if not final and "<think>" in buffer and "</think>" not in buffer:
        return "", ya_emitido

    limpio = limpiar_markdown_respuesta(limpiar_razonamiento(buffer))

    if final:
        if len(limpio) <= ya_emitido:
            return "", ya_emitido
        return limpio[ya_emitido:], len(limpio)

    corte = 0
    for m in re.finditer(r"[.!?…](?=\s)", limpio):
        if m.end() > ya_emitido:
            corte = m.end()

    if corte <= ya_emitido:
        return "", ya_emitido

    return limpio[ya_emitido:corte], corte


def _cantidad_de_args(raw) -> float:
    """
    Lee el importe que viene en los argumentos de un tool call.

    El modelo puede mandar `null`, una cadena vacía o directamente texto. Todo
    eso se trata como "no ha dicho el importe" (0.0), que es el caso que
    `iniciar_bizum` resuelve preguntándoselo al usuario.
    """
    if raw is None or raw == "":
        return 0.0
    try:
        return round(float(raw), 2)
    except (ValueError, TypeError):
        return 0.0


def explicar_error(e: Exception) -> str:
    """
    Traduce una excepción a algo que el usuario pueda leer y accionar.

    El caso que importa es que el servidor del modelo no esté levantado: la
    librería devuelve un escueto "Connection error."
    """
    if isinstance(e, APIConnectionError):
        return (
            f"no puedo contactar con el modelo de lenguaje en {client.base_url}. "
            "Comprueba que Ollama esté arrancado (./arrancar_ollama.sh) y vuelve a intentarlo."
        )

    return str(e)


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
    Detecta peticiones de Bizum con distintos órdenes naturales, permitiendo
    cantidades opcionales y conceptos explícitos.

    Ejemplos admitidos:
    - Haz un Bizum a María López de 20 euros
    - Envía 20 euros a María López por Bizum
    - Haz un bizum a María con concepto cena
    - Págale 15 euros a Ana por Bizum para la cena
    - Haz un bizum de 2 euros a Lucia G. con concepto papel
    """
    texto = mensaje.strip()

    if "bizum" not in normalizar_texto(texto):
        return None

    patrones = [
        # 1. Haz un Bizum a María López de 20 euros [para la cena]
        #   Es la forma más natural de pedir un Bizum.
        (
            r"^(?:haz|hacer|envia|envía|manda|mandar)\s+"
            r"(?:un\s+)?bizum\s+a\s+"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+?)\s+(?:de|por)\s+"
            r"(?P<cantidad>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?"
            r"(?:\s+(?:para|de|por|con\s+concepto)\s+(?P<concepto>.+))?$"
        ),

        # 2. Caso general: cantidad antes o después del destinatario, con
        #    concepto explícito al final. Cubre "haz un bizum de 20 euros a Ana
        #    para la cena".
        (
            r"^(?:haz|hacer|envia|envía|manda|mandar)\s+"
            r"(?:un\s+)?bizum\s+"
            r"(?:(?:de|por)\s+(?P<cantidad_ini>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?\s+a\s+|\s+a\s+)?"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+?)"
            r"(?:\s+(?:de|por)\s+(?P<cantidad_med>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?)?"
            r"\s+(?:con\s+concepto|concepto|para|de|por)\s+(?P<concepto>.+)$"
        ),

        # 3. Envía 20 euros a María López por Bizum con concepto cena
        (
            r"^(?:envia|envía|manda|mandar)\s+"
            r"(?P<cantidad>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?\s+a\s+"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+?)\s+(?:por\s+)?bizum\s*"
            r"(?:(?:para|de|por|con\s+concepto)\s+(?P<concepto>.+))?$"
        ),

        # 4. Sin cantidad pero con concepto explícito
        (
            r"^(?:haz|hacer|envia|envía|manda|mandar)\s+"
            r"(?:un\s+)?bizum\s+a\s+"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+?)\s+"
            r"(?:con\s+concepto|para|de)\s+(?P<concepto>.+)$"
        ),

        # 5. Envía 20 euros a María López por Bizum sin concepto
        (
            r"^(?:envia|envía|manda|mandar)\s+"
            r"(?P<cantidad>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?\s+a\s+"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+?)\s+(?:por\s+)?bizum$"
        ),

        # 6. Envía 20 € por Bizum a María López
        (
            r"^(?:envia|envía|manda|mandar)\s+"
            r"(?P<cantidad>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?\s+"
            r"(?:por\s+)?bizum\s+a\s+"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+?)$"
        ),
        
        # 7. Haz un bizum de 20 euros a María López
        (
            r"^(?:haz|hacer|envia|envía|manda|mandar)\s+"
            r"(?:un\s+)?bizum\s+(?:de|por)\s+"
            r"(?P<cantidad>\d+(?:[,.]\d+)?)\s*(?:€|euros?)?\s+a\s+"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+?)$"
        ),
        
        # 8. Bizum de 20 euros para María
        (
            r"^(?:un\s+)?bizum\s+(?:de\s+)?(?P<cantidad>\d+"
            r"(?:[,.]\d+)?)\s*(?:€|euros?)?\s+para\s+"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+)"
        ),
        
        # 9. Págale 15 euros a Ana por Bizum
        (
            r"^(?:p[aá]gale|pagar)\s+(?P<cantidad>\d+"
            r"(?:[,.]\d+)?)\s*(?:€|euros?)?\s+a\s+"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+?)\s+(?:por\s+)?bizum"
        ),

        # 10. Solo destinatario sin cantidad
        (
            r"^(?:haz|hacer|envia|envía|manda|mandar)\s+"
            r"(?:un\s+)?bizum\s+a\s+"
            r"(?P<destinatario>[a-zA-ZáéíóúÁÉÍÓÚñÑ\s.]+)$"
        ),
    ]

    for patron in patrones:
        match = re.search(patron, texto, flags=re.IGNORECASE)

        if not match:
            continue

        datos = match.groupdict()
        destinatario = datos.get("destinatario", "").strip()
        
        # Eliminar una preposición "a" inicial sobrante
        destinatario = re.sub(r"^a\s+", "", destinatario, flags=re.IGNORECASE).strip()

        # Limpiamos posibles restos de conectores al final del nombre del destinatario
        destinatario = re.sub(r"\s+(?:de|por|con\s+concepto|concepto|para)$", "", destinatario, flags=re.IGNORECASE).strip()

        # Capturar cantidad
        cantidad_txt = datos.get("cantidad") or datos.get("cantidad_ini") or datos.get("cantidad_med")
        cantidad = 0.0
        if cantidad_txt:
            try:
                cantidad = round(float(cantidad_txt.replace(",", ".")), 2)
            except ValueError:
                cantidad = 0.0

        # Capturar concepto de forma segura
        concepto = ""
        if datos.get("concepto"):
            concepto = datos["concepto"].strip()

        return {
            "destinatario": destinatario,
            "cantidad": cantidad,
            "concepto": concepto,
        }

    return None

def limpiar_razonamiento(texto: str) -> str:
    """
    Qwen3 puede emitir bloques <think>...</think>.
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
        self.intentos_pin_restantes: int = INTENTOS_PIN

    def dejar_bizum_pendiente(self, destinatario: str, cantidad: float, concepto: str) -> None:
        """Deja un Bizum a la espera del PIN y reinicia el contador de intentos."""
        self.bizum_pendiente = {
            "destinatario": destinatario,
            "cantidad": cantidad,
            "concepto": concepto,
        }
        self.intentos_pin_restantes = INTENTOS_PIN


    def asegurar_system_en_historial(self) -> None:
        """Añade el system prompt una sola vez, antes del primer turno."""
        if not self.historial:
            self.historial.append({
                "role": "system",
                "content": self.system,
            })

    def recortar_historial(self) -> None:
        """
        Limita el historial a los últimos turnos, conservando el system prompt.

        Sin esto, una conversación larga desborda los 8192 tokens de contexto.
        El corte no puede caer en cualquier sitio. Un mensaje `tool` sin el
        `assistant` con `tool_calls` que lo provocó deja el historial
        inconsistente y la API lo rechaza, así que se avanza hasta el
        siguiente mensaje de `user`, que siempre abre un turno completo.
        """
        if len(self.historial) <= MAX_MENSAJES_HISTORIAL + 1:
            return

        if self.historial[0].get("role") != "system":
            return

        system, resto = self.historial[0], self.historial[1:]

        corte = len(resto) - MAX_MENSAJES_HISTORIAL
        while corte < len(resto) and resto[corte].get("role") != "user":
            corte += 1

        if corte >= len(resto):
            return  # no hay ningún punto de corte seguro; mejor no tocar nada

        self.historial = [system] + resto[corte:]


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

            self.dejar_bizum_pendiente(contacto, cantidad, concepto)

            texto = (
                f"Perfecto. Vas a enviar {formatear_euros(cantidad)} "
                f"a {contacto}. Introduce tu PIN de seguridad para confirmar el envío."
            )

            await self.responder_directo(texto)
            await self.emitir({"type": "pedir_pin"})
            return True

        if es_cancelacion_bizum(mensaje_usuario):
            pendiente = self.correccion_contacto_pendiente
            self.correccion_contacto_pendiente = None

            destinatario = pendiente["destinatario_original"]
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
        Si hay un Bizum pendiente, el chat solo acepta cancelarlo.
        La confirmación real se hace por websocket con el PIN.
        """
        if not self.bizum_pendiente:
            return False

        if es_cancelacion_bizum(mensaje_usuario):
            self.bizum_pendiente = None
            await self.responder_directo("De acuerdo, cancelo el Bizum. No se ha enviado nada.")
            return True

        # Si escribe otra cosa, le recordamos que use la interfaz segura o cancele.
        texto = (
            "Tienes un Bizum pendiente. Por favor, introduce tu PIN en el panel de seguridad "
            "o escribe 'cancela' si prefieres anularlo."
        )
        await self.responder_directo(texto)
        return True
    
    
    async def validar_pin_bizum(self, pin: str) -> None:
        """Valida el PIN introducido de forma segura y ejecuta el Bizum si es correcto."""
        if not self.bizum_pendiente:
            await self.responder_directo("No hay ningún envío de Bizum pendiente.")
            return

        # PIN simulado.
        # Vive en config.py para que no aparezca en el código del agente.
        if pin != PIN_BIZUM:
            self.intentos_pin_restantes -= 1

            if self.intentos_pin_restantes <= 0:
                self.bizum_pendiente = None
                await self.responder_directo(
                    "PIN incorrecto. Has agotado los intentos y, por tu seguridad, "
                    "he cancelado el envío. No se ha movido dinero."
                )
                return

            restantes = self.intentos_pin_restantes
            
            # 1. Guardamos el mensaje en una variable
            mensaje_error = (
                f"PIN incorrecto. Te {'queda' if restantes == 1 else 'quedan'} "
                f"{restantes} {'intento' if restantes == 1 else 'intentos'}."
            )
            
            # 2. Lo mostramos en el chat como siempre
            await self.responder_directo(mensaje_error)
            
            # 3. Y LO ENVIAMOS AL MODAL añadiendo "error": mensaje_error
            await self.emitir({"type": "pedir_pin", "error": mensaje_error})
            return

        # Si el PIN es correcto, procedemos a ejecutar la herramienta
        pendiente = self.bizum_pendiente
        self.bizum_pendiente = None
        pendiente["confirmado"] = True

        await self.emitir({"type": "inicio_respuesta"})

        # Ejecutamos la herramienta con los datos confirmados
        salida_json = await ejecutar_tool("enviar_bizum", pendiente, self.emitir)
        salida = json.loads(salida_json)

        if salida.get("estado") == "ok":
            concepto = salida.get("concepto")
            # Añadimos el concepto al texto de respuesta si existe y no es vacío
            txt_concepto = f" con concepto {concepto}" if concepto else ""
            texto = (
                f"Bizum enviado correctamente a {salida['destinatario']} "
                f"por {formatear_euros(salida['cantidad'])}{txt_concepto}. "
                f"Tu nuevo saldo es {formatear_euros(salida['nuevo_saldo'])}."
            )
        else:
            texto = (
                salida.get("motivo")
                or salida.get("error")
                or f"No se ha podido enviar el Bizum. Respuesta interna: {salida}"
            )

        await self.responder_directo(texto, emitir_inicio=False)
    

    async def iniciar_bizum(
        self,
        destinatario_original: str,
        cantidad: float,
        concepto: str = "",
        *,
        registrar=None,
        emitir_inicio: bool = True,
    ) -> None:
        """
        Arranca un envío de Bizum: valida importe, resuelve el contacto y, si
        todo cuadra, lo deja pendiente del PIN.

        `registrar` es la única diferencia entre las dos vías de entrada(Atajo o LLM).
        Cuando el origen es un tool call del LLM, toda llamada a herramienta
        necesita su `tool_result` en el historial antes de añadir nada más o la
        API rechaza la siguiente petición: se le pasa una función que lo anota.
        Desde el atajo del backend no hace falta y se omite.
        """
        def anotar(payload: dict) -> None:
            if registrar is not None:
                registrar(payload)

        async def decir(texto: str) -> None:
            await self.responder_directo(texto, emitir_inicio=emitir_inicio)

        destinatario_original = (destinatario_original or "").strip()
        concepto = (concepto or "").strip()

        # El importe va primero, antes de resolver el contacto y antes de pedir
        # el PIN: si no cabe en los límites no hay operación que confirmar
        if cantidad <= 0:
            # NO se deja `bizum_pendiente`. Ese estado significa "esperando el
            # PIN", y `gestionar_bizum_pendiente` intercepta con él todos los
            # turnos siguientes.
            anotar({
                "estado": "error",
                "motivo": "Falta la cantidad. Se le ha preguntado al usuario.",
            })
            await decir(f"¿Cuánto dinero quieres enviarle a {destinatario_original}?")
            return

        error_importe = validar_importe_bizum(cantidad)
        if error_importe:
            anotar({"estado": "error", "motivo": error_importe})
            await decir(error_importe)
            return

        # Consulta la agenda en la BD: fuera del event loop, como el resto de
        # accesos a datos. `buscar_contacto_bizum` se queda síncrona para que `tests/test_bizum.py` la siga llamando tal cual.
        validacion = await asyncio.to_thread(buscar_contacto_bizum, destinatario_original)

        if validacion["estado"] == "no_encontrado":
            anotar({"estado": "no_encontrado", "destinatario": destinatario_original})
            await decir(
                f"No encuentro a “{destinatario_original}” como contacto de Bizum. "
                "Revisa el nombre o usa un contacto con el que ya hayas hecho Bizum."
            )
            return

        if validacion["estado"] == "sugerencia":
            contacto_sugerido = validacion["contacto"]

            self.correccion_contacto_pendiente = {
                "destinatario_original": destinatario_original,
                "contacto_sugerido": contacto_sugerido,
                "cantidad": cantidad,
                "concepto": concepto,
            }

            anotar({
                "estado": "sugerencia_contacto",
                "destinatario_original": destinatario_original,
                "contacto_sugerido": contacto_sugerido,
                "cantidad": cantidad,
                "concepto": concepto,
            })
            await decir(
                f"No encuentro exactamente “{destinatario_original}”. "
                f"¿Querías decir {contacto_sugerido}?"
            )
            return

        destinatario = validacion["contacto"]
        self.dejar_bizum_pendiente(destinatario, cantidad, concepto)

        anotar({
            "estado": "pendiente_confirmacion",
            "destinatario": destinatario,
            "cantidad": cantidad,
            "concepto": concepto,
        })
        await decir(
            f"Vas a enviar {formatear_euros(cantidad)} "
            f"a {destinatario}. Por favor, introduce tu PIN de seguridad para confirmar el envío."
        )

        # Señal al frontend para que despliegue el teclado numérico del PIN.
        await self.emitir({"type": "pedir_pin"})

    async def preparar_bizum_desde_backend(self, datos: dict) -> None:
        """Atajo del backend: la petición viene de la regex, no del LLM."""
        await self.iniciar_bizum(
            datos.get("destinatario", ""),
            round(float(datos.get("cantidad", 0)), 2),
            datos.get("concepto", ""),
        )
        
    async def procesar(self, mensaje_usuario: str) -> None:
        """
        Punto de entrada de un turno de conversación.

        Nada debe escapar de aquí: una excepción que suba hasta el handler del
        WebSocket cierra la conexión, el frontend reconecta solo y se construye
        un Agente nuevo, así que el usuario pierde todo el historial sin que
        nada se lo diga.
        """
        try:
            await self._procesar(mensaje_usuario)
        except Exception:
            traceback.print_exc()

            # Una operación de dinero a medias es peor que volver a empezarla.
            self.bizum_pendiente = None
            self.correccion_contacto_pendiente = None

            await self.emitir({
                "type": "error",
                "detalle": "algo ha fallado al procesar tu mensaje. "
                           "Si estabas haciendo un Bizum, vuelve a pedírmelo: no se ha enviado nada.",
            })
            await self.emitir({"type": "fin_respuesta", "texto": ""})
    
    
    async def _procesar(self, mensaje_usuario: str) -> None:
            # Todos los mensajes entran en el historial,
            # también los gestionados directamente por el backend.
            self.asegurar_system_en_historial()
            self.historial.append({
                "role": "user",
                "content": mensaje_usuario,
            })
        
            if await self.gestionar_correccion_contacto_pendiente(mensaje_usuario):
                return
            
            # 1. primero comprobamos si estamos esperando la cantidad de un Bizum incompleto
            if self.bizum_pendiente and self.bizum_pendiente.get("cantidad", 0.0) == 0.0:
                match_cant = re.search(r"(\d+(?:[,.]\d+)?)", mensaje_usuario)
                if match_cant:
                    cantidad = round(float(match_cant.group(1).replace(",", ".")), 2)
                    destinatario = self.bizum_pendiente["destinatario"]
                    concepto = self.bizum_pendiente["concepto"]
                    self.bizum_pendiente = None # Limpiamos el temporal
                    
                    # Reanudamos el flujo normal con la cantidad y el concepto conservados
                    await self.iniciar_bizum(destinatario, cantidad, concepto)
                    return

            # 2. despues gestionamos el Bizum pendiente real (esperando PIN o cancelación)
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

            ultimo_representable: tuple[str, dict] | None = None
            visual_emitido = False

            try:
                for _ in range(MAX_ITERACIONES_AGENTE):
                    self.recortar_historial()

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
                    emitido = 0          

                    async for chunk in stream:
                        if not chunk.choices:
                            continue

                        delta = chunk.choices[0].delta

                        if delta.content:
                            texto_iteracion += delta.content

                            if not tool_calls_locales:
                                trozo, emitido = frases_emitibles(texto_iteracion, emitido)
                                if trozo:
                                    await self.emitir({"type": "texto", "delta": trozo})

                        if delta.tool_calls:
                            for tool_call in delta.tool_calls:
                                idx = tool_call.index

                                if idx not in tool_calls_locales:
                                    tool_calls_locales[idx] = {"id": None, "name": None, "arguments": ""}

                                tc_local = tool_calls_locales[idx]
                                if tool_call.id:
                                    tc_local["id"] = tool_call.id
                                if tool_call.function:
                                    if tool_call.function.name:
                                        tc_local["name"] = tool_call.function.name
                                    if tool_call.function.arguments:
                                        tc_local["arguments"] += tool_call.function.arguments

                    tool_calls_locales = {
                        idx: tc for idx, tc in tool_calls_locales.items() if tc["name"]
                    }
                    for idx, tc in tool_calls_locales.items():
                        if not tc["id"]:
                            tc["id"] = f"call_{idx}"

                    if tool_calls_locales and emitido:
                        await self.emitir({"type": "descartar_texto"})
                        emitido = 0

                    if not tool_calls_locales:
                        texto_iteracion = limpiar_razonamiento(texto_iteracion)
                        texto_iteracion = limpiar_markdown_respuesta(texto_iteracion)

                        if not texto_iteracion.strip():
                            if not reintento_vacio:
                                reintento_vacio = True
                                continue
                            texto_iteracion = (
                                "Perdona, no he podido redactar la respuesta. "
                                "¿Puedes repetir la pregunta?"
                            )
                            emitido = 0      

                        self.historial.append({"role": "assistant", "content": texto_iteracion})
                        texto_final += texto_iteracion

                        trozo, emitido = frases_emitibles(texto_iteracion, emitido, final=True)
                        if trozo:
                            await self.emitir({"type": "texto", "delta": trozo})
                        break

                    self.historial.append({
                        "role": "assistant",
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

                    respondidos: set[str] = set()

                    for tc in tool_calls_locales.values():
                        try:
                            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError as e:
                            self.historial.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "name": tc["name"],
                                "content": json.dumps({
                                    "error": f"Los argumentos no son JSON válido: {e}. Reintenta la llamada."
                                }, ensure_ascii=False),
                            })
                            respondidos.add(tc["id"])
                            continue

                        if tc["name"] == "enviar_bizum":
                            def registrar(payload: dict, _tc=tc) -> None:
                                self.historial.append({
                                    "role": "tool",
                                    "tool_call_id": _tc["id"],
                                    "name": _tc["name"],
                                    "content": json.dumps(payload, ensure_ascii=False),
                                })
                                respondidos.add(_tc["id"])

                            await self.iniciar_bizum(
                                args.get("destinatario", ""),
                                _cantidad_de_args(args.get("cantidad")),
                                args.get("concepto", ""),
                                registrar=registrar,
                                emitir_inicio=False,   
                            )

                            for pendiente in tool_calls_locales.values():
                                if pendiente["id"] in respondidos:
                                    continue
                                self.historial.append({
                                    "role": "tool",
                                    "tool_call_id": pendiente["id"],
                                    "name": pendiente["name"],
                                    "content": json.dumps({
                                        "estado": "no_ejecutada",
                                        "motivo": "Hay un envío de Bizum en curso pendiente "
                                                "del PIN. Esta herramienta no se ha ejecutado; "
                                                "vuelve a pedirla si sigue haciendo falta.",
                                    }, ensure_ascii=False),
                                })
                            return

                        await self.emitir({"type": "tool_inicio", "nombre": tc["name"]})

                        salida = str(await ejecutar_tool(tc["name"], args, self.emitir))

                        if tc["name"] in ("consultar_movimientos", "analizar_suscripciones"):
                            try:
                                ultimo_representable = (tc["name"], json.loads(salida))
                            except json.JSONDecodeError:
                                ultimo_representable = None
                        elif tc["name"] in ("mostrar_grafico", "mostrar_tabla"):
                            visual_emitido = '"estado": "ok"' in salida

                        if len(salida) > MAX_CHARS_TOOL_RESULT:
                            salida = (
                                salida[:MAX_CHARS_TOOL_RESULT]
                                + " …[resultado recortado: pide menos columnas o agrega los datos]"
                            )

                        self.historial.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                            "content": salida,
                        })
                        respondidos.add(tc["id"])

            except Exception as e:
                await self.emitir({"type": "error", "detalle": explicar_error(e)})
                await self.emitir({"type": "fin_respuesta", "texto": ""})
                return

            if not texto_final.strip():
                texto_final = (
                    "No he conseguido completar la respuesta. "
                    "¿Puedes reformular la pregunta?"
                )
                self.historial.append({"role": "assistant", "content": texto_final})
                await self.emitir({"type": "texto", "delta": texto_final})

            await self.emitir({"type": "fin_respuesta", "texto": texto_final.strip()})

            if not visual_emitido and ultimo_representable:
                nombre_tool, resultado = ultimo_representable
                columnas, filas = normalizar(nombre_tool, resultado)

                if filas:
                    await self.emitir({"type": "visual_generando"})

                tipo, payload, razonamiento = await generar_visual(
                    client, MODELO, mensaje_usuario, columnas, filas,
                    extra_body=EXTRA_BODY,
                )

                if tipo == "grafico" and payload:
                    await ejecutar_tool(
                        "mostrar_grafico",
                        {"spec": payload, "razonamiento": razonamiento},
                        self.emitir,
                    )
                elif tipo == "tabla" and payload:
                    await ejecutar_tool(
                        "mostrar_tabla",
                        {**payload, "razonamiento": razonamiento},
                        self.emitir,
                    )
                elif filas:
                    await self.emitir({"type": "visual_cancelado"})