"""
Generación de la base de datos ficticia.

Genera ~24 meses de movimientos realistas para un cliente con ciertos patrones:

- Nómina mensual (día 28) y alquiler (día 2).
- Recibos periódicos: luz, agua, internet, Netflix, Spotify, gimnasio.
- Seguro del coche UNA vez al año.
- Gasolina varias veces al mes en gasolineras reales.
- Compras de supermercado, restaurantes, farmacia, ropa, ocio, Bizums...

El reto sugiere apoyarse en un LLM para generar los datos de forma masiva.
Aquí usamos un generador determinista, pero el catálogo de comercios/descripciones
fue redactado con ayuda de un LLM.

Uso:  python -m backend.seed
"""

import random
import sqlite3
from datetime import date, timedelta

from .config import DB_PATH

random.seed(42)  # reproducible

HOY = date.today()

# 24 meses de histórico.
INICIO = (HOY.replace(day=1) - timedelta(days=730)).replace(day=1)

# Netflix sube de precio a mitad del histórico.
SUBIDA_NETFLIX = (HOY.replace(day=1) - timedelta(days=365)).replace(day=1)
PRECIO_NETFLIX_ANTIGUO = -13.99
PRECIO_NETFLIX_NUEVO = -15.99

# ── Catálogo de comercios por categoría ──────────────────────────────────────
GASOLINERAS = ["Repsol", "Cepsa", "BP", "Galp", "Shell"]
SUPERMERCADOS = ["Mercadona", "Carrefour", "Lidl", "Dia", "Alcampo"]
RESTAURANTES = ["Bar Casa Pepe", "100 Montaditos", "La Tagliatella", "Burger King",
                "Telepizza", "Café de la Plaza", "Restaurante El Patio"]
FARMACIAS = ["Farmacia Central", "Farmacia San Juan"]
ROPA = ["Zara", "Pull&Bear", "Decathlon", "Primark"]
OCIO = ["Cines Kinépolis", "Steam", "Amazon", "Fnac", "Entradas.com"]
TRANSPORTE = ["Metropolitano de Granada", "Renfe", "Uber", "Cabify"]
CONTACTOS_BIZUM = ["María López", "Carlos Ruiz", "Ana Torres", "Javi Molina", "Lucía G."]


def _meses(desde: date, hasta: date):
    """Itera el día 1 de cada mes del rango."""
    d = desde.replace(day=1)
    while d <= hasta:
        yield d
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def generar_movimientos() -> list[tuple]:
    movs: list[tuple] = []

    def add(fecha: date, importe: float, categoria: str, comercio: str, desc: str):
        if INICIO <= fecha <= HOY:
            movs.append((fecha.isoformat(), round(importe, 2), categoria, comercio, desc))

    for mes in _meses(INICIO, HOY):
        # Ingresos y recibos fijos
        add(mes.replace(day=min(28, 28)), 2200.00, "nomina", "Empresa Tecnosur SL", "Nómina mensual")
        add(mes.replace(day=2), -650.00, "alquiler", "Inmobiliaria Genil", "Alquiler piso")
        add(mes.replace(day=5),
            PRECIO_NETFLIX_ANTIGUO if mes < SUBIDA_NETFLIX else PRECIO_NETFLIX_NUEVO,
            "suscripciones", "Netflix", "Suscripción mensual Netflix")
        add(mes.replace(day=7), -10.99, "suscripciones", "Spotify", "Suscripción mensual Spotify")
        add(mes.replace(day=3), -34.90, "gimnasio", "Gimnasio VivaFit", "Cuota mensual gimnasio")
        add(mes.replace(day=12), -21.50, "internet", "Digi", "Fibra + móvil")
        # Luz cada mes, agua cada 2 meses (importe variable)
        add(mes.replace(day=15), -random.uniform(38, 82), "luz", "Endesa", "Recibo de luz")
        if mes.month % 2 == 0:
            add(mes.replace(day=18), -random.uniform(22, 40), "agua", "Emasagra", "Recibo de agua")

        # Gasolina: 4-7 repostajes/mes
        for _ in range(random.randint(4, 7)):
            add(mes + timedelta(days=random.randint(0, 27)),
                -random.uniform(35, 78), "gasolina", random.choice(GASOLINERAS),
                "Repostaje combustible")

        # Supermercado: 8-12 compras/mes
        for _ in range(random.randint(8, 12)):
            add(mes + timedelta(days=random.randint(0, 27)),
                -random.uniform(12, 95), "supermercado", random.choice(SUPERMERCADOS),
                "Compra supermercado")

        # Restaurantes: 3-8/mes
        for _ in range(random.randint(3, 8)):
            add(mes + timedelta(days=random.randint(0, 27)),
                -random.uniform(8, 55), "restaurantes", random.choice(RESTAURANTES),
                "Comida/cena fuera")

        # Resto de categorías con probabilidad
        for _ in range(random.randint(1, 3)):
            add(mes + timedelta(days=random.randint(0, 27)),
                -random.uniform(3, 28), "farmacia", random.choice(FARMACIAS), "Farmacia")
        for _ in range(random.randint(0, 2)):
            add(mes + timedelta(days=random.randint(0, 27)),
                -random.uniform(15, 120), "ropa", random.choice(ROPA), "Compra de ropa")
        for _ in range(random.randint(1, 4)):
            add(mes + timedelta(days=random.randint(0, 27)),
                -random.uniform(5, 60), "ocio", random.choice(OCIO), "Ocio y entretenimiento")
        for _ in range(random.randint(2, 6)):
            add(mes + timedelta(days=random.randint(0, 27)),
                -random.uniform(1.4, 25), "transporte", random.choice(TRANSPORTE), "Transporte")

        # Bizums enviados y recibidos
        for _ in range(random.randint(1, 3)):
            c = random.choice(CONTACTOS_BIZUM)
            add(mes + timedelta(days=random.randint(0, 27)),
                -random.uniform(5, 80), "bizum_enviado", c, f"Bizum enviado a {c}")
        for _ in range(random.randint(0, 2)):
            c = random.choice(CONTACTOS_BIZUM)
            add(mes + timedelta(days=random.randint(0, 27)),
                random.uniform(5, 80), "bizum_recibido", c, f"Bizum recibido de {c}")

    # Seguro del coche: una vez al año, en febrero
    for anio in range(INICIO.year, HOY.year + 1):
        add(date(anio, 2, 10), -418.60, "seguro_coche", "Línea Directa",
            "Seguro anual del coche - Renovación póliza")

    movs.sort(key=lambda m: m[0])
    return movs


def crear_bd():
    DB_PATH.unlink(missing_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            importe REAL NOT NULL,
            categoria TEXT NOT NULL,
            comercio TEXT NOT NULL,
            descripcion TEXT NOT NULL
        );
        CREATE INDEX idx_mov_fecha ON movimientos(fecha);
        CREATE INDEX idx_mov_categoria ON movimientos(categoria);
        CREATE TABLE cliente (
            id INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            iban TEXT NOT NULL,
            saldo REAL NOT NULL
        );
    """)

    movs = generar_movimientos()
    conn.executemany(
        "INSERT INTO movimientos (fecha, importe, categoria, comercio, descripcion) VALUES (?,?,?,?,?)",
        movs,
    )

    saldo = 2500.00 + sum(m[1] for m in movs)  # saldo inicial + suma de movimientos
    conn.execute(
        "INSERT INTO cliente (id, nombre, iban, saldo) VALUES (1, ?, ?, ?)",
        ("Alejandro García", "ES91 2103 0000 0011 2233 4455", round(saldo, 2)),
    )
    conn.commit()
    conn.close()
    print(f"✅ Base de datos creada: {DB_PATH}")
    print(f"   {len(movs)} movimientos entre {movs[0][0]} y {movs[-1][0]}")
    print(f"   Saldo actual: {saldo:,.2f} €")


if __name__ == "__main__":
    crear_bd()
