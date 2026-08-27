"""
Evaluación de la precisión del asistente (30 pts del baremo).

Lanza las preguntas de `preguntas.jsonl` contra el agente real.

PUNTÚAN TRES COSAS, porque fallan por motivos distintos:

  1. ELECCIÓN DE HERRAMIENTA. ¿Llamó a la que tocaba? Preguntar por
     suscripciones y que se ponga a escribir SQL es un fallo aunque el número
     salga bien. Se registra con un espía sobre `ejecutar_tool`, así se ven
     también los atajos del backend, que no pasan por el historial del LLM.
     Un caso puede admitir varias herramientas válidas (lista).

  2. RESPUESTA FINAL. Es lo único que oye el usuario. Se comprueba que los
     valores esperados aparezcan en el texto, con formato español.

  3. GRÁFICO CUANDO TOCA. Son 20 pts del baremo (lógica visual + gráficos sin
     plantillas), más que ninguna otra cosa salvo la precisión. Los casos con
     `espera_grafico` se puntúan en los dos sentidos: no pintar donde hay
     varios valores comparables es un fallo, y pintar donde la respuesta es un
     único dato también, porque el criterio del propio system prompt lo
     prohíbe. Se lee del evento `grafico` del WebSocket, así que mide lo que
     de verdad le llega al frontend.

  4. SPEC PINTABLE. Que llegue un gráfico no significa que se vea: una spec
     sin `mark` pasa el filtro de `tools.py` (que solo exige `data.values`,
     a propósito, para admitir layer y concat) y revienta después en
     `vega-embed`, donde ya no lo registra nadie. Puntúa, y va aparte de la
     métrica 3 porque son fallos de cosas distintas: uno es el modelo
     decidiendo si toca gráfico, el otro el modelo redactando la spec.

Y SE MIDE, SIN PUNTUAR, un indicador de DIAGNÓSTICO: se ejecutan la consulta
del agente y una de referencia escrita a mano y se comparan los valores. Sirve
para saber POR QUÉ falló una respuesta, pero no cuenta para la nota: el modelo
puede elegir una interpretación distinta y defendible (otro periodo, otra
forma de calcular una media) y divergir de la referencia sin estar equivocado.
Nunca se compara el TEXTO del SQL: hay muchas consultas correctas distintas
para la misma pregunta.

La base de datos es reproducible (`random.seed(42)` en seed.py), así que los
valores esperados se recalculan en cada ejecución y no caducan. Eso sí, hay
que regenerarla antes de evaluar: los datos son relativos a `date.today()` y
con una BD de hace unos días "esta semana" sale vacío.

Uso:
    python -m tests.evaluar
    python -m tests.evaluar --filtro suscripciones
    python -m tests.evaluar --repeticiones 3     # mide también la varianza
    python -m tests.evaluar --modelo otro:8b     # si algún día se compara
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

PREGUNTAS = Path(__file__).resolve().parent / "preguntas.jsonl"


# ──────────────────────────────────────────────────────────────────────────
# Comparación de resultados
# ──────────────────────────────────────────────────────────────────────────

def es_numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def valores_de(resultado: dict) -> list:
    """
    Aplana el resultado de una consulta a una lista comparable: números
    redondeados a 2 decimales y textos normalizados. Se ordena para que el
    orden de las columnas no cuente como diferencia.
    """
    filas = resultado.get("filas") or []
    valores = []
    for fila in filas:
        for celda in fila:
            if es_numero(celda):
                valores.append(round(float(celda), 2))
            elif celda is not None:
                valores.append(normalizar(str(celda)))
    return sorted(valores, key=repr)


def normalizar(texto: str) -> str:
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def formatos_es(valor: float) -> list[str]:
    """Formas en que un importe puede aparecer en la respuesta hablada."""
    entero, decimales = f"{abs(valor):.2f}".split(".")
    con_miles = f"{int(entero):,}".replace(",", ".")
    return [
        f"{con_miles},{decimales}",      # 1.234,56
        f"{entero},{decimales}",         # 1234,56
        f"{con_miles}",                  # 1.234  (si redondea)
        f"{abs(valor):.2f}",             # 1234.56
    ]


def aparece_en(valor, texto: str) -> bool:
    texto_norm = normalizar(texto)
    if es_numero(valor):
        return any(normalizar(f) in texto_norm for f in formatos_es(valor))
    return normalizar(str(valor)) in texto_norm


# ──────────────────────────────────────────────────────────────────────────
# Gráficos
# ──────────────────────────────────────────────────────────────────────────

def _busca_clave(spec, clave: str) -> bool:
    """True si `clave` aparece con valor no vacío en la spec o en alguna vista anidada."""
    if not isinstance(spec, dict):
        return False

    if spec.get(clave):
        return True

    for contenedor in ("layer", "hconcat", "vconcat", "concat"):
        hijos = spec.get(contenedor)
        if isinstance(hijos, list) and any(_busca_clave(h, clave) for h in hijos):
            return True

    # facet / repeat envuelven la vista real en spec.spec
    if isinstance(spec.get("spec"), dict):
        return _busca_clave(spec["spec"], clave)

    return False


def spec_pintable(spec) -> bool:
    """
    Una spec necesita marca y encoding, no solo datos, para llegar a pintarse.

    `tools.py` solo exige `data.values` —a propósito, para admitir layer y
    concat— así que una spec con `mark` a nulo pasa el filtro del backend,
    el agente da el gráfico por mostrado y luego falla en `vega-embed`, donde
    ya no lo ve nadie. Aquí sí queda registrado.

    Se busca cada clave en todo el árbol en vez de exigirlas en la raíz: en un
    `layer` la marca vive en los hijos y el encoding puede estar heredado del
    padre, y exigir ambas en el mismo nivel daría fallos falsos.
    """
    return _busca_clave(spec, "mark") and _busca_clave(spec, "encoding")


# ──────────────────────────────────────────────────────────────────────────
# Ejecución de un caso
# ──────────────────────────────────────────────────────────────────────────

async def ejecutar_caso(caso: dict, Agente, ejecutar_sql_seguro, detectar_pagos, espia) -> dict:
    eventos: list[dict] = []

    async def emitir(evento):
        eventos.append(evento)

    espia.clear()
    agente = Agente(emitir)

    inicio = time.perf_counter()
    for turno in caso["turnos"]:
        await agente.procesar(turno)
    latencia = time.perf_counter() - inicio

    sql_agente = next(
        (e["sql"] for e in reversed(eventos) if e["type"] == "sql" and not e.get("error")),
        None,
    )
    finales = [e for e in eventos if e["type"] == "fin_respuesta"]
    respuesta = finales[-1]["texto"] if finales else ""
    graficos = [e for e in eventos if e["type"] == "grafico"]

    resultado = {
        "id": caso["id"],
        "tipo": caso["tipo"],
        "pregunta": caso["turnos"][-1],
        "latencia": round(latencia, 1),
        "herramientas": list(espia),
        "sql_agente": sql_agente,
        "respuesta": respuesta,
        "graficos": len(graficos),
        "razonamiento": graficos[-1].get("razonamiento", "") if graficos else "",
        "tool_ok": None,
        "valores_ok": None,
        "respuesta_ok": None,
        "grafico_ok": None,
        "spec_ok": None,
    }

    # 1. Herramienta correcta. Admite una lista: a veces hay más de una vía
    # legítima ("¿cuánto pago de media de luz?" se puede responder con SQL o
    # con el detector de pagos recurrentes, y ninguna de las dos está mal).
    if "tool" in caso:
        if caso["tool"] is None:
            resultado["tool_ok"] = not espia
        else:
            aceptadas = caso["tool"] if isinstance(caso["tool"], list) else [caso["tool"]]
            resultado["tool_ok"] = any(t in espia for t in aceptadas)

    # Valores esperados: de la consulta de referencia o de la tool de análisis
    referencia = ejecutar_sql_seguro(caso["sql"]) if caso.get("sql") else None
    esperados = None
    numeros_esperados: list = []

    if referencia is not None:
        esperados = valores_de(referencia)
        filas_ref = referencia.get("filas") or []
        # Solo la primera fila y solo sus números. La primera fila basta: en un
        # ranking, que acierte el primero ya demuestra que leyó el resultado.
        # Y solo números porque las columnas de texto no se dicen literalmente
        # ("2026-03" se responde como "marzo"), lo que daría fallos falsos.
        numeros_esperados = [c for c in (filas_ref[0] if filas_ref else []) if es_numero(c)]
    elif caso.get("referencia_suscripciones"):
        valor = round(float(detectar_pagos()[caso["referencia_suscripciones"]]), 2)
        esperados = [valor]
        numeros_esperados = [valor]

    resultado["esperados"] = esperados

    # 2. Precisión de ejecución del SQL. Es un DIAGNÓSTICO, no parte de la nota:
    # sirve para saber por qué falló una respuesta, pero no puntúa, porque el
    # modelo puede elegir una interpretación distinta y defendible (otro
    # periodo, otra forma de calcular una media) y divergir de la referencia
    # sin estar equivocado. Lo que cuenta es la respuesta que oye el usuario.
    if referencia is not None and caso.get("tool") == "consultar_movimientos":
        if sql_agente:
            obtenidos = valores_de(ejecutar_sql_seguro(sql_agente))
            resultado["valores_ok"] = obtenidos == esperados
            resultado["obtenidos"] = obtenidos
        else:
            resultado["valores_ok"] = False

    # 3. La respuesta traslada bien las cifras
    if caso.get("comprobar_respuesta") is False:
        pass          # pregunta de gráfico: el texto no puede listar todos los meses
    elif numeros_esperados:
        resultado["respuesta_ok"] = all(aparece_en(v, respuesta) for v in numeros_esperados)
    elif caso.get("contiene"):
        resultado["respuesta_ok"] = all(
            normalizar(t) in normalizar(respuesta) for t in caso["contiene"]
        )
    elif caso.get("contiene_alguno"):
        resultado["respuesta_ok"] = any(
            normalizar(t) in normalizar(respuesta) for t in caso["contiene_alguno"]
        )

    # 4. Gráfico cuando toca (20 pts del baremo: lógica visual + sin plantillas).
    # Se puntúa en los dos sentidos: no pintar donde hay varios valores
    # comparables es un fallo, y pintar donde la respuesta es un único dato
    # también, porque el criterio del prompt dice explícitamente que no.
    if "espera_grafico" in caso:
        resultado["grafico_ok"] = bool(graficos) == caso["espera_grafico"]

    # Una spec puede llegar y aun así no pintarse. Va separado de `grafico_ok`
    # porque son fallos de cosas distintas: uno es del modelo decidiendo si
    # toca gráfico, el otro del modelo redactando la spec.
    if graficos:
        resultado["spec_ok"] = all(spec_pintable(e["spec"]) for e in graficos)

        if not resultado["spec_ok"]:
            # Las claves de la spec rota, para saber qué faltó sin volcar los
            # datos enteros en el informe.
            resultado["claves_spec"] = [
                sorted(e["spec"].keys()) if isinstance(e["spec"], dict) else type(e["spec"]).__name__
                for e in graficos if not spec_pintable(e["spec"])
            ]

    return resultado


# ──────────────────────────────────────────────────────────────────────────
# Informe
# ──────────────────────────────────────────────────────────────────────────

def marca(valor) -> str:
    return "  " if valor is None else ("OK" if valor else "XX")


def es_fallo(r: dict) -> bool:
    """Un caso falla si suspende cualquiera de las métricas que puntúan."""
    return any(r[clave] is False
               for clave in ("tool_ok", "respuesta_ok", "grafico_ok", "spec_ok"))


def informe(resultados: list[dict], modelo: str) -> bool:
    print()
    print("=" * 100)
    print(f"RESULTADOS  ·  modelo: {modelo}")
    print("=" * 100)
    print(f"{'id':28} {'tipo':13} {'tool':5} {'sql':4} {'resp':5} {'graf':5} {'seg':>6}  pregunta")
    print("-" * 100)

    for r in resultados:
        print(f"{r['id']:28} {r['tipo']:13} "
              f"{marca(r['tool_ok']):5} {marca(r['valores_ok']):4} {marca(r['respuesta_ok']):5} "
              f"{marca(r['grafico_ok']):5} "
              f"{r['latencia']:6.1f}  {r['pregunta'][:36]}")

    def tasa(clave):
        vistos = [r[clave] for r in resultados if r[clave] is not None]
        if not vistos:
            return None, 0, 0
        aciertos = sum(1 for v in vistos if v)
        return 100 * aciertos / len(vistos), aciertos, len(vistos)

    print()
    print("-" * 100)
    print("PUNTUACIÓN")
    for clave, etiqueta in (("tool_ok", "Elección de herramienta"),
                            ("respuesta_ok", "Respuesta final correcta"),
                            ("grafico_ok", "Gráfico cuando toca"),
                            ("spec_ok", "Specs que llegan a pintarse")):
        porcentaje, aciertos, total = tasa(clave)
        if porcentaje is not None:
            print(f"  {etiqueta:32} {aciertos:3}/{total:<3}  {porcentaje:5.1f} %")

    porcentaje, aciertos, total = tasa("valores_ok")
    if porcentaje is not None:
        print(f"  {'(diagnóstico) SQL vs referencia':32} {aciertos:3}/{total:<3}  {porcentaje:5.1f} %")

    latencias = [r["latencia"] for r in resultados]
    print(f"{'Latencia mediana / máxima':34} {statistics.median(latencias):5.1f} s / "
          f"{max(latencias):.1f} s")

    print()
    print("Por tipo de pregunta:")
    tipos = sorted({r["tipo"] for r in resultados})
    for tipo in tipos:
        del_tipo = [r for r in resultados if r["tipo"] == tipo]
        fallos = [r for r in del_tipo if es_fallo(r)]
        print(f"   {tipo:14} {len(del_tipo) - len(fallos):2}/{len(del_tipo):<2} correctas")

    fallidos = [r for r in resultados if es_fallo(r)]
    if fallidos:
        print()
        print("FALLOS EN DETALLE")
        print("-" * 100)
        for r in fallidos:
            print(f"\n[{r['id']}] {r['pregunta']}")
            print(f"   herramientas: {r['herramientas'] or 'ninguna'}")
            if r.get("sql_agente"):
                print(f"   sql:          {' '.join(r['sql_agente'].split())[:150]}")
            if r.get("esperados") is not None:
                print(f"   esperado:     {r['esperados']}")
            if r.get("obtenidos") is not None:
                print(f"   obtenido:     {r['obtenidos']}")
            if r["grafico_ok"] is False:
                if r["graficos"]:
                    detalle = f"{r['graficos']} gráfico(s), no se esperaba ninguno"
                else:
                    detalle = "ninguno, se esperaba uno"
                print(f"   gráfico:      {detalle}")
            if r["spec_ok"] is False:
                print(f"   spec:         llega sin mark o sin encoding (no se pinta)")
                for claves in r.get("claves_spec", []):
                    print(f"                 claves recibidas: {claves}")
            if r["razonamiento"]:
                print(f"   razonamiento: {r['razonamiento'][:120]}")
            print(f"   respuesta:    {r['respuesta'][:150]}")

    return not fallidos


# ──────────────────────────────────────────────────────────────────────────

async def principal(args) -> int:
    # El modelo se lee al importar config, así que se fija antes.
    if args.modelo:
        os.environ["MODELO"] = args.modelo

    from backend import agent as modulo_agente
    from backend.agent import Agente
    from backend.analitica import detectar_pagos_recurrentes
    from backend.config import MODELO
    from backend.database import ejecutar_sql_seguro

    # Espía sobre el dispatcher: registra TODA herramienta ejecutada, incluidas
    # las de los atajos del backend, que no pasan por el historial del LLM.
    espia: list[str] = []
    original = modulo_agente.ejecutar_tool

    async def ejecutar_tool_espiada(nombre, entrada, emitir):
        espia.append(nombre)
        return await original(nombre, entrada, emitir)

    modulo_agente.ejecutar_tool = ejecutar_tool_espiada

    casos = [json.loads(linea) for linea in PREGUNTAS.read_text(encoding="utf-8").splitlines() if linea.strip()]
    if args.filtro:
        casos = [c for c in casos if args.filtro in c["tipo"] or args.filtro in c["id"]]

    print(f"Evaluando {len(casos)} preguntas con {MODELO}"
          f"{f' x{args.repeticiones} repeticiones' if args.repeticiones > 1 else ''}...")

    resultados = []
    for indice, caso in enumerate(casos, 1):
        for repeticion in range(args.repeticiones):
            print(f"  [{indice}/{len(casos)}] {caso['id']}", end="\r", flush=True)
            resultado = await ejecutar_caso(
                caso, Agente, ejecutar_sql_seguro, detectar_pagos_recurrentes, espia
            )
            if args.repeticiones > 1:
                resultado["id"] = f"{caso['id']}#{repeticion + 1}"
            resultados.append(resultado)

    todo_ok = informe(resultados, MODELO)

    if args.guardar:
        destino = Path(args.guardar)
        destino.write_text(
            json.dumps({"modelo": MODELO, "resultados": resultados}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nResultados guardados en {destino}")

    return 0 if todo_ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evalúa la precisión del asistente bancario.")
    parser.add_argument("--modelo", help="Sobreescribe MODELO (p. ej. llama3.1)")
    parser.add_argument("--filtro", help="Solo los casos cuyo tipo o id contenga este texto")
    parser.add_argument("--repeticiones", type=int, default=1, help="Repite cada pregunta N veces")
    parser.add_argument("--guardar", help="Guarda los resultados en un JSON")
    sys.exit(asyncio.run(principal(parser.parse_args())))
