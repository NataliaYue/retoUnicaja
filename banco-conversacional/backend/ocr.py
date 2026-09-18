import os
import re
import pytesseract
from PIL import Image

# 1. Tesseract CMD
ruta_tesseract = os.getenv("TESSERACT_CMD")
if ruta_tesseract:
    pytesseract.pytesseract.tesseract_cmd = ruta_tesseract

# 2. Tessdata (idiomas)
ruta_tessdata = os.getenv("TESSDATA_PREFIX_CUSTOM")
if ruta_tessdata:
    os.environ["TESSDATA_PREFIX"] = ruta_tessdata

def limpiar_texto_recibo(texto: str, max_chars: int = 2000) -> str:
    """Elimina ruido de OCR, líneas vacías y recorta el texto a lo esencial."""
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    lineas_filtradas = [l for l in lineas if len(l) > 2 and not re.match(r"^[^a-zA-Z0-9]+$", l)]
    resumen = "\n".join(lineas_filtradas)
    return resumen[:max_chars].strip()

def procesar_archivo_recibo(ruta_archivo: str) -> dict:
    """Extrae y resume el texto de una imagen usando Tesseract."""
    try:
        with Image.open(ruta_archivo) as img:
            texto_crudo = pytesseract.image_to_string(img, lang="spa")

        texto_util = limpiar_texto_recibo(texto_crudo)

        if not texto_util:
            return {
                "estado": "error",
                "motivo": "No se ha podido reconocer texto legible en el recibo.",
            }

        return {
            "estado": "ok",
            "texto": texto_util,
        }
    except Exception as e:
        return {"estado": "error", "motivo": str(e)}