"""
APIs bancarias ficticias (requisito "Operaciones", 10 pts del baremo).

Simulan los endpoints internos del banco que un asistente real invocaría.
Se exponen como funciones Python que el agente llama vía tool calling; en
producción serían llamadas HTTP a los servicios del banco, por eso devuelven
dicts con la forma típica de una respuesta de API (estado + datos).

Nota de seguridad: son las ÚNICAS funciones con acceso de escritura a la BD,
y siempre con parámetros ligados (?), nunca con SQL construido por el LLM.
"""

from datetime import date

from .database import conexion_escritura, conexion_lectura

"""
APIs bancarias ficticias (requisito "Operaciones", 10 pts del baremo).

Simulan los endpoints internos del banco que un asistente real invocaría.
Se exponen como funciones Python que el agente llama vía tool calling; en
producción serían llamadas HTTP a los servicios del banco, por eso devuelven
dicts con la forma típica de una respuesta de API (estado + datos).

Nota de seguridad: son las ÚNICAS funciones con acceso de escritura a la BD,
y siempre con parámetros ligados (?), nunca con SQL construido por el LLM.
"""

from datetime import date

from .database import conexion_escritura, conexion_lectura


def api_consultar_saldo() -> dict:
    """GET /api/v1/cuentas/saldo (ficticio)."""
    with conexion_lectura() as conn:
        fila = conn.execute("SELECT nombre, iban, saldo FROM cliente WHERE id = 1").fetchone()
    return {
        "estado": "ok",
        "titular": fila["nombre"],
        "iban": fila["iban"],
        "saldo": round(fila["saldo"], 2),
        "moneda": "EUR",
    }

def api_listar_contactos_bizum() -> dict:
    """
    GET /api/v1/bizum/contactos (ficticio).

    Devuelve los contactos conocidos de Bizum a partir del histórico
    de movimientos. Sirve para validar destinatarios antes de enviar dinero.
    """
    with conexion_lectura() as conn:
        filas = conn.execute("""
            SELECT DISTINCT comercio
            FROM movimientos
            WHERE categoria IN ('bizum_enviado', 'bizum_recibido')
            ORDER BY comercio
        """).fetchall()

    return {
        "estado": "ok",
        "contactos": [fila["comercio"] for fila in filas],
    }

def api_enviar_bizum(destinatario: str, cantidad: float, concepto: str = "") -> dict:
    """
    POST /api/v1/bizum/enviar (ficticio).

    Validaciones de negocio (las mismas que haría el banco real):
    - Importe dentro de los límites de Bizum (0,50 € – 1.000 €).
    - Saldo suficiente.
    Si todo es correcto: descuenta el saldo y registra el movimiento.
    """
    if not destinatario or not destinatario.strip():
        return {"estado": "error", "motivo": "Falta el destinatario."}

    cantidad = round(float(cantidad), 2)
    if not (0.50 <= cantidad <= 1000.00):
        return {
            "estado": "error",
            "motivo": "El importe de un Bizum debe estar entre 0,50 € y 1.000 €.",
        }

    with conexion_escritura() as conn:
        saldo = conn.execute("SELECT saldo FROM cliente WHERE id = 1").fetchone()["saldo"]
        if saldo < cantidad:
            return {
                "estado": "error",
                "motivo": f"Saldo insuficiente: el saldo actual es {saldo:.2f} € y el envío es de {cantidad:.2f} €.",
            }

        nuevo_saldo = round(saldo - cantidad, 2)
        conn.execute("UPDATE cliente SET saldo = ? WHERE id = 1", (nuevo_saldo,))
        conn.execute(
            "INSERT INTO movimientos (fecha, importe, categoria, comercio, descripcion) "
            "VALUES (?,?,?,?,?)",
            (
                date.today().isoformat(),
                -cantidad,
                "bizum_enviado",
                destinatario.strip(),
                f"Bizum enviado a {destinatario.strip()}"
                + (f" - {concepto.strip()}" if concepto and concepto.strip() else ""),
            ),
        )
        conn.commit()

    return {
        "estado": "ok",
        "destinatario": destinatario.strip(),
        "cantidad": cantidad,
        "concepto": concepto or None,
        "nuevo_saldo": nuevo_saldo,
    }

def api_consultar_saldo() -> dict:
    """GET /api/v1/cuentas/saldo (ficticio)."""
    with conexion_lectura() as conn:
        fila = conn.execute("SELECT nombre, iban, saldo FROM cliente WHERE id = 1").fetchone()
    return {
        "estado": "ok",
        "titular": fila["nombre"],
        "iban": fila["iban"],
        "saldo": round(fila["saldo"], 2),
        "moneda": "EUR",
    }


def api_enviar_bizum(destinatario: str, cantidad: float, concepto: str = "") -> dict:
    """
    POST /api/v1/bizum/enviar (ficticio).

    Validaciones de negocio (las mismas que haría el banco real):
    - Importe dentro de los límites de Bizum (0,50 € – 1.000 €).
    - Saldo suficiente.
    Si todo es correcto: descuenta el saldo y registra el movimiento.
    """
    if not destinatario or not destinatario.strip():
        return {"estado": "error", "motivo": "Falta el destinatario."}

    cantidad = round(float(cantidad), 2)
    if not (0.50 <= cantidad <= 1000.00):
        return {
            "estado": "error",
            "motivo": "El importe de un Bizum debe estar entre 0,50 € y 1.000 €.",
        }

    with conexion_escritura() as conn:
        saldo = conn.execute("SELECT saldo FROM cliente WHERE id = 1").fetchone()["saldo"]
        if saldo < cantidad:
            return {
                "estado": "error",
                "motivo": f"Saldo insuficiente: el saldo actual es {saldo:.2f} € y el envío es de {cantidad:.2f} €.",
            }

        nuevo_saldo = round(saldo - cantidad, 2)
        conn.execute("UPDATE cliente SET saldo = ? WHERE id = 1", (nuevo_saldo,))
        conn.execute(
            "INSERT INTO movimientos (fecha, importe, categoria, comercio, descripcion) "
            "VALUES (?,?,?,?,?)",
            (
                date.today().isoformat(),
                -cantidad,
                "bizum_enviado",
                destinatario.strip(),
                f"Bizum enviado a {destinatario.strip()}"
                + (f" - {concepto.strip()}" if concepto and concepto.strip() else ""),
            ),
        )
        conn.commit()

    return {
        "estado": "ok",
        "destinatario": destinatario.strip(),
        "cantidad": cantidad,
        "concepto": concepto or None,
        "nuevo_saldo": nuevo_saldo,
    }
