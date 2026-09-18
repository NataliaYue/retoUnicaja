"""
Servidor FastAPI.

- GET  /          → sirve el frontend (frontend/index.html).
- WS   /ws        → canal de chat en tiempo real. Cada conexión tiene su
                    propio Agente y por tanto su propio historial.
- GET  /api/saldo → endpoint REST auxiliar para pintar el saldo al cargar.

Protocolo WebSocket:

  Cliente → Servidor:  { "mensaje": "¿cuánto gasté en gasolina este mes?" }

  Servidor → Cliente:
    { "type": "inicio_respuesta" }
    { "type": "texto", "delta": "Has gastado " }         (muchos, en streaming)
    { "type": "sql", "sql": "SELECT ...", "proposito": "...", "error": null }
    { "type": "visual_generando" }        (el motor visual está preparando algo)
    { "type": "grafico", "spec": {...vega-lite...}, "razonamiento": "..." }
    { "type": "tabla", "title": "...", "columnas": [{"campo","titulo","formato"}],
                       "filas": [{...}], "razonamiento": "..." }
    { "type": "visual_cancelado" }        (no salió nada pintable: retirar el aviso)
    { "type": "saldo", "valor": 3421.55 }
    { "type": "fin_respuesta", "texto": "Has gastado 84,20 € ..." }
    { "type": "error", "detalle": "..." }

Arranque:  uvicorn backend.main:app --reload
"""
import asyncio
import traceback

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .agent import Agente
from .banking_api import api_consultar_saldo
from .config import DB_PATH, TIMEOUT_WS_SEGUNDOS


FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Comprobaciones de arranque.

    Sin `banco.db` la aplicación arranca pero todas las consultas fallan, así
    que es mejor no arrancar y decir cómo generarla.
    """
    if not DB_PATH.exists():
        raise RuntimeError(
            "No existe banco.db. Genera los datos primero:  python -m backend.seed"
        )
    yield


app = FastAPI(
    title="Habla con tu dinero — Reto Unicaja & UGR",
    lifespan=lifespan,
)


@app.get("/")
def index():
    return FileResponse(FRONTEND)

@app.get("/unicaja.png")
def logo():
    return FileResponse(FRONTEND.parent / "unicaja.png")

@app.get("/api/saldo")
def saldo():
    return api_consultar_saldo()

@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    await ws.accept()

    async def emitir(evento: dict):
        await ws.send_json(evento)

    agente = Agente(emitir)

    # Saludo inicial + saldo para la cabecera.
    datos = await asyncio.to_thread(api_consultar_saldo)
    await emitir({"type": "saldo", "valor": datos["saldo"]})

    try:
        while True:
            datos_ws = await asyncio.wait_for(
                ws.receive_json(),
                timeout=TIMEOUT_WS_SEGUNDOS,
            )

            # Extraemos el tipo de evento si no viene, asumimos que es 'chat'
            tipo_evento = datos_ws.get("type", "chat")

            # Red de seguridad: si un turno falla, se informa y se sigue
            # escuchando. Dejar que la excepción suba cerraría el WebSocket y
            # el usuario perdería el historial completo sin ningún aviso.
            try:
                if tipo_evento == "chat":
                    mensaje = (datos_ws.get("mensaje") or "").strip()
                    if mensaje:
                        # Solo los mensajes normales de chat van al historial
                        await agente.procesar(mensaje)

                elif tipo_evento == "auth_bizum":
                    pin = datos_ws.get("pin")
                    if pin:
                        # Este método aísla la contraseña del LLM
                        await agente.validar_pin_bizum(pin)
                
                elif tipo_evento == "imagen":
                    import os
                    import base64
                    import tempfile
                    from .ocr import procesar_archivo_recibo

                    datos_b64 = datos_ws.get("base64", "")
                    
                    if datos_b64 and "," in datos_b64:
                        contenido = base64.b64decode(datos_b64.split(",")[1])
                        
                        # Crear archivo temporal
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                            tmp.write(contenido)
                            tmp_path = tmp.name

                        try:
                            # Procesar la imagen
                            resultado_ocr = await asyncio.to_thread(procesar_archivo_recibo, tmp_path)
                            
                            if resultado_ocr.get("estado") == "ok":
                                texto_recibo = resultado_ocr["texto"]
                                
                                prompt_usuario = (
                                    "Acabo de subir un recibo. Actúa como un extractor de datos, "
                                    "ignora todo el texto irrelevante o legal y extrae estrictamente "
                                    "esta información del siguiente bloque de texto:\n"
                                    f"\"\"\"\n{texto_recibo}\n\"\"\"\n"
                                    "Dime únicamente:\n"
                                    "- Comercio.\n"
                                    "- Importe total a pagar (busca el valor definitivo).\n"
                                    "- Categoría del gasto.\n"
                                    "Responde de forma natural pero muy breve."
                                )
                            else:
                                prompt_usuario = (
                                    f"He intentado subir un recibo pero no se ha podido leer: "
                                    f"{resultado_ocr.get('motivo')}."
                                )
                                
                            if resultado_ocr.get("estado") == "ok":
                                texto_recibo = resultado_ocr["texto"]
                                
                                print("\n" + "="*40)
                                print("✅ TEXTO EXTRAÍDO POR TESSERACT:")
                                print(texto_recibo)
                                print("="*40 + "\n")

                                prompt_usuario = (
                                    f"He subido una imagen de un recibo. Este es el texto extraído:\n"
                                    f"\"\"\"\n{texto_recibo}\n\"\"\"\n"
                                    f"Por favor, dime de qué comercio es, el importe total y a qué categoría corresponde."
                                )
                            else:
                                motivo_error = resultado_ocr.get('motivo')
                                
                                print("\n" + "="*40)
                                print("❌ ERROR EN TESSERACT:")
                                print(motivo_error)
                                print("="*40 + "\n")

                                prompt_usuario = (
                                    f"He intentado subir un recibo pero no se ha podido leer: {motivo_error}."
                                )

                            await agente.procesar(prompt_usuario)

                        finally:
                            # Limpieza del archivo temporal usando 'os'
                            if os.path.exists(tmp_path):
                                os.remove(tmp_path)

            except WebSocketDisconnect:
                raise
            except Exception:
                traceback.print_exc()
                await emitir({
                    "type": "error",
                    "detalle": "no he podido completar la operación. Inténtalo de nuevo.",
                })
                await emitir({"type": "fin_respuesta", "texto": ""})

    except asyncio.TimeoutError:
        await emitir({"type": "error", "detalle": "Sesión cerrada por inactividad."})
        await ws.close()
    except WebSocketDisconnect:
        pass