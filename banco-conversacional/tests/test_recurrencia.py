"""
Validación de la detección de pagos recurrentes contra muchas semillas.

Los umbrales de `analitica.py` se ajustaron mirando UNA base de datos. Eso no
demuestra que generalicen: podrían estar sobreajustados a esa realización
concreta del azar. Aquí se regenera el histórico con N semillas distintas y se
mide, contra la verdad conocida por construcción, cuántos falsos positivos y
falsos negativos produce el detector.

Verdad de terreno: en `seed.py` los pagos periódicos están cableados (Netflix
el día 5, alquiler el día 2...) y no dependen del azar. Lo que sí cambia con la
semilla es todo lo demás: cuántas compras de supermercado hay cada mes, en qué
días caen los repostajes, los importes variables de luz y agua, los bizums...
Es decir, exactamente el material del que pueden salir falsos positivos.

Uso:  python -m tests.test_recurrencia [n_semillas]
"""

import random
import sys
from datetime import date

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from backend.analitica import analizar_recurrencia  # noqa: E402
from backend.seed import generar_movimientos  # noqa: E402

# Comercios que SON recurrentes por construcción en seed.py (solo gastos: la
# nómina es un ingreso y el detector solo mira importes negativos).
RECURRENTES_REALES = {
    "Inmobiliaria Genil": "mensual",   # alquiler, día 2
    "Netflix": "mensual",              # día 5
    "Spotify": "mensual",              # día 7
    "Gimnasio VivaFit": "mensual",     # día 3
    "Digi": "mensual",                 # día 12
    "Endesa": "mensual",               # luz, día 15, importe variable
    "Emasagra": "bimestral",           # agua, día 18, meses pares
    "Línea Directa": "anual",          # seguro del coche, 10 de febrero
}

# Nombre del pago anual, que se sigue contabilizando aparte en el informe
# porque es el más frágil: con solo dos cobros en 24 meses, su detección
# depende por completo de que el importe se repita exacto.
RECURRENTE_ANUAL = "Línea Directa"


def movimientos_de_semilla(semilla: int) -> list[dict]:
    random.seed(semilla)
    return [
        {"fecha": f, "importe": i, "categoria": c, "comercio": co}
        for f, i, c, co, _ in generar_movimientos()
        if i < 0
    ]


def main(n_semillas: int = 50) -> int:
    hoy = date.today()

    total_fp = 0
    total_fn = 0
    cadencias_mal = 0
    semillas_perfectas = 0
    subida_netflix_detectada = 0
    falsos_positivos: dict[str, int] = {}
    falsos_negativos: dict[str, int] = {}
    anual_detectado = 0

    for semilla in range(n_semillas):
        movimientos = movimientos_de_semilla(semilla)
        resultado = analizar_recurrencia(movimientos, hoy=hoy)

        todos = resultado["suscripciones"] + resultado["recibos_fijos"]
        detectados = {p["comercio"]: p["cadencia"] for p in todos}

        if detectados.get(RECURRENTE_ANUAL) == "anual":
            anual_detectado += 1

        # La subida de Netflix está cableada en seed.py, no depende del azar:
        # si deja de detectarse es que se ha roto la detección de cambios de
        # precio, no que la semilla haya sido desafortunada.
        netflix = next(
            (p for p in todos if p["comercio"] == "Netflix"),
            None,
        )
        if netflix and netflix["cambio_precio"] == {
            "importe_anterior": 13.99, "importe_actual": 15.99, "diferencia": 2.0
        }:
            subida_netflix_detectada += 1

        fp = set(detectados) - set(RECURRENTES_REALES)
        fn = set(RECURRENTES_REALES) - set(detectados)
        mal = {
            c for c in set(detectados) & set(RECURRENTES_REALES)
            if detectados[c] != RECURRENTES_REALES[c]
        }

        total_fp += len(fp)
        total_fn += len(fn)
        cadencias_mal += len(mal)

        if not fp and not fn and not mal:
            semillas_perfectas += 1

        for c in fp:
            falsos_positivos[c] = falsos_positivos.get(c, 0) + 1
        for c in fn:
            falsos_negativos[c] = falsos_negativos.get(c, 0) + 1

    print(f"Semillas probadas: {n_semillas}")
    print(f"Semillas perfectas: {semillas_perfectas}/{n_semillas} "
          f"({100 * semillas_perfectas / n_semillas:.0f} %)")
    print()
    print(f"Falsos negativos (recurrentes no detectados): {total_fn}")
    for comercio, veces in sorted(falsos_negativos.items(), key=lambda kv: -kv[1]):
        print(f"    {comercio:26} {veces:3}/{n_semillas}")
    print(f"Falsos positivos (detectados sin serlo):      {total_fp}")
    for comercio, veces in sorted(falsos_positivos.items(), key=lambda kv: -kv[1]):
        print(f"    {comercio:26} {veces:3}/{n_semillas}")
    print(f"Cadencias mal clasificadas:                   {cadencias_mal}")
    print()
    print(f"Seguro anual detectado como tal en {anual_detectado}/{n_semillas} semillas")
    print(f"Subida de precio de Netflix detectada en {subida_netflix_detectada}/{n_semillas}")

    todo_ok = (
        total_fp == 0
        and total_fn == 0
        and cadencias_mal == 0
        and anual_detectado == n_semillas
        and subida_netflix_detectada == n_semillas
    )
    return 0 if todo_ok else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 50))
