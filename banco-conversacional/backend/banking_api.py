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
    """
    if not destinatario or not destinatario.strip():
        return {"estado": "error", "motivo": "Falta el destinatario."}

    cantidad = round(float(cantidad), 2)
    if not (0.50 <= cantidad <= 1000.00):
        return {
            "estado": "error",
            "motivo": "El importe de un Bizum debe estar entre 0,50 € y 1.000 €.",
        }

    LIMITE_DIARIO_BOT = 500.00  # Límite de seguridad del asistente

    with conexion_escritura() as conn:
        # 1. Comprobar el límite diario acumulado
        hoy = date.today().isoformat()
        
        # Como los gastos se guardan en negativo, sumamos y cambiamos el signo
        fila_gastado = conn.execute(
            "SELECT SUM(importe) as total_hoy FROM movimientos "
            "WHERE categoria = 'bizum_enviado' AND fecha = ?",
            (hoy,)
        ).fetchone()
        
        # Si no hay envíos hoy, será None. Si los hay, será un número negativo.
        gastado_hoy = abs(fila_gastado["total_hoy"] or 0.0)

        if gastado_hoy + cantidad > LIMITE_DIARIO_BOT:
            return {
                "estado": "error",
                "motivo": f"Operación denegada. El límite diario del asistente es {LIMITE_DIARIO_BOT:.2f} € y ya has enviado {gastado_hoy:.2f} € hoy."
            }

        # 2. Comprobar saldo suficiente
        saldo = conn.execute("SELECT saldo FROM cliente WHERE id = 1").fetchone()["saldo"]
        if saldo < cantidad:
            return {
                "estado": "error",
                "motivo": f"Saldo insuficiente: el saldo actual es {saldo:.2f} € y el envío es de {cantidad:.2f} €.",
            }

        # 3. Ejecutar el movimiento
        nuevo_saldo = round(saldo - cantidad, 2)
        conn.execute("UPDATE cliente SET saldo = ? WHERE id = 1", (nuevo_saldo,))
        conn.execute(
            "INSERT INTO movimientos (fecha, importe, categoria, comercio, descripcion) "
            "VALUES (?,?,?,?,?)",
            (
                hoy,
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
