"""
Capa de acceso a datos.

Punto clave del diseño (IA Responsable): el SQL que genera el LLM se ejecuta
SIEMPRE a través de `ejecutar_sql_seguro`, que aplica defensa en profundidad:

1. Conexión SQLite abierta en modo SOLO LECTURA (mode=ro): aunque todo lo demás
   fallara, la BD es físicamente inmutable desde esta vía.
2. Solo se admite UNA sentencia, que debe empezar por SELECT o WITH.
3. Lista negra de palabras clave de escritura/administración.
4. Límite duro de filas devueltas (el LLM no necesita más y así controlamos
   el tamaño del contexto y la latencia).

Las operaciones legítimas de escritura (Bizum) NO pasan por aquí: usan su
propia conexión de escritura en `banking_api.py`, con parámetros ligados
(nunca SQL construido por el LLM).
"""

import re
import sqlite3

from .config import DB_PATH
from contextlib import closing
# Tope de filas devueltas al LLM. Ojo: no es solo una cuestión de latencia.
# El resultado entra entero en el historial de la conversación, y con un
# contexto de 8192 tokens (ver arrancar_ollama.sh) del que el system prompt y
# las tools ya ocupan ~2.900, un resultado de 200 filas desborda el contexto en
# un solo turno: Ollama trunca por delante, se pierde el system prompt y la
# calidad de las respuestas se cae sin que nada lo avise.
# Para responder una pregunta agregada nunca hacen falta tantas filas.
MAX_FILAS = 50

# Palabras que jamás deberían aparecer en una consulta de lectura.
_PROHIBIDAS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|"
    r"vacuum|reindex|trigger)\b",
    re.IGNORECASE,
)


def conexion_lectura() -> sqlite3.Connection:
    """Conexión de solo lectura. Cualquier intento de escritura lanza excepción."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def conexion_escritura() -> sqlite3.Connection:
    """Conexión normal, reservada a las APIs bancarias ficticias (no al LLM)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ejecutar_sql_seguro(sql: str) -> dict:
    """
    Ejecuta una consulta de solo lectura generada por el LLM.

    Devuelve un dict serializable:
      { "columnas": [...], "filas": [[...], ...], "num_filas": n }
    o { "error": "..." } si algo falla. El error se devuelve al LLM para que
    se autocorrija en la siguiente iteración del bucle agéntico.
    """
    limpio = sql.strip().rstrip(";").strip()

    if ";" in limpio:
        return {"error": "Solo se permite una única sentencia SQL."}

    primera = limpio.split(None, 1)[0].lower() if limpio else ""
    if primera not in ("select", "with"):
        return {"error": "Solo se permiten consultas de lectura (SELECT / WITH)."}

    if _PROHIBIDAS.search(limpio):
        return {"error": "La consulta contiene palabras clave no permitidas."}

    try:
        # Usamos closing() para forzar el cierre del descriptor al salir del bloque
        with closing(conexion_lectura()) as conn:
            cur = conn.execute(limpio)
            filas = cur.fetchmany(MAX_FILAS)
            columnas = [d[0] for d in cur.description] if cur.description else []
            return {
                "columnas": columnas,
                "filas": [list(f) for f in filas],
                "num_filas": len(filas),
                "truncado": len(filas) == MAX_FILAS,
            }
    except sqlite3.Error as e:
        # Se lo devolvemos al LLM tal cual: es sorprendentemente bueno
        # corrigiendo su propio SQL a partir del mensaje de error.
        return {"error": f"Error de SQLite: {e}"}
