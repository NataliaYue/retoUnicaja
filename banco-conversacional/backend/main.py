"""
Servidor FastAPI.

- GET  /          → sirve el frontend (frontend/index.html).
- WS   /ws        → canal de chat en tiempo real. Cada conexión tiene su
                    propio Agente (y por tanto su propio historial).
- GET  /api/saldo → endpoint REST auxiliar para pintar el saldo al cargar.

Protocolo WebSocket (JSON en ambos sentidos):

  Cliente → Servidor:  { "mensaje": "¿cuánto gasté en gasolina este mes?" }

  Servidor → Cliente:
    { "type": "inicio_respuesta" }
    { "type": "texto", "delta": "Has gastado " }         (muchos, en streaming)
    { "type": "sql", "sql": "SELECT ...", "proposito": "...", "error": null }
    { "type": "grafico", "spec": {...vega-lite...}, "razonamiento": "..." }
    { "type": "saldo", "valor": 3421.55 }
    { "type": "fin_respuesta", "texto": "Has gastado 84,20 € ..." }
    { "type": "error", "detalle": "..." }

Arranque:  uvicorn backend.main:app --reload
"""
import asyncio

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .agent import Agente
from .banking_api import api_consultar_saldo
from .config import DB_PATH


app = FastAPI(title="Habla con tu dinero — Reto Unicaja & UGR")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.on_event("startup")
def comprobar_bd():
    if not DB_PATH.exists():
        raise RuntimeError(
            "No existe banco.db. Genera los datos primero:  python -m backend.seed"
        )


@app.get("/")
def index():
    return FileResponse(FRONTEND)


@app.get("/api/saldo")
def saldo():
    return api_consultar_saldo()


@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    await ws.accept()

    async def emitir(evento: dict):
        await ws.send_json(evento)

    agente = Agente(emitir)

    # Saludo inicial + saldo para la cabecera
    datos = api_consultar_saldo()
    await emitir({"type": "saldo", "valor": datos["saldo"]})

    try:
        while True:
            # Espera 5 minutos (300 segundos) máximo
            datos_ws = await asyncio.wait_for(ws.receive_json(), timeout=300.0)
            
            # Extraemos el tipo de evento (si no viene, asumimos que es 'chat')
            tipo_evento = datos_ws.get("type", "chat")
            
            if tipo_evento == "chat":
                mensaje = (datos_ws.get("mensaje") or "").strip()
                if mensaje:
                    # Solo los mensajes normales de chat van al historial
                    await agente.procesar(mensaje)
                    
            elif tipo_evento == "auth_bizum":
                pin = datos_ws.get("pin")
                if pin:
                    # Este método aislará la contraseña del LLM (lo crearemos ahora)
                    await agente.validar_pin_bizum(pin)
                    
    except asyncio.TimeoutError:
        await emitir({"type": "error", "detalle": "Sesión cerrada por inactividad."})
        await ws.close()
    except WebSocketDisconnect:
        pass