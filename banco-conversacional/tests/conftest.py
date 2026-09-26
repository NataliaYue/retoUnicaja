"""
Configuración compartida de los tests.

La copia de seguridad de `banco.db` vive aquí y no dentro de cada test: los
envíos de Bizum mueven saldo de verdad e insertan movimientos, así que la base
tiene que quedar como estaba aunque un test falle a mitad. Con `autouse` se
aplica sola y con `scope="session"` la copia se hace una única vez.
"""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DB_PATH  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def bd_intacta():
    copia = DB_PATH.with_suffix(".db.bak_test")
    shutil.copy2(DB_PATH, copia)
    yield
    shutil.copy2(copia, DB_PATH)
    copia.unlink()
