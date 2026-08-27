# TODO — Reto 2: Analítica Avanzada y Generative UI

**Cátedra IA Responsable en Finanzas** (Unicaja & UGR, con NVIDIA) · https://catedraiaunicaja.ugr.es/reto2.html

## Baremo oficial

| Criterio | Pts |
|---|---|
| Precisión de consultas | **30** |
| Conversación | 15 |
| Agilidad | 15 |
| Operaciones | 10 |
| Lógica visual | 10 |
| Gráficos sin plantillas | 10 |
| Vídeo | 5 |
| Memoria | 5 |

## Qué pide el enunciado del Reto 2

> "Consultar movimientos bancarios mediante lenguaje natural. El asistente generará interfaces
> dinámicas (**Generative UI**) automáticas con **gráficos y tablas** que refuercen la respuesta de voz."
>
> "Procesamiento de consultas complejas (**Gastos, Comparativas, Suscripciones**)."
>
> "Dominio de **Analítica Predictiva** y GenUI." · Stack: Unicaja & Google Cloud.

**Tres consecuencias directas** sobre lo que hay construido:
1. **Tablas**: la GenUI esperada incluye tablas, no solo gráficos. Hoy solo hay Vega-Lite.
2. **Suscripciones**: es uno de los tres tipos de consulta que nombran y no hay ninguna lógica
   de detección de recurrencia. Es la pieza de "analítica avanzada" que falta.
3. **Predictiva**: el sistema es 100 % retrospectivo. Una proyección de gasto encaja directa.

---

# ✅ FASE 0 — Parar la hemorragia — **HECHA** (18 ago)

Fallos que podían arruinar una demo en directo.

- [x] **Crash que tumbaba el WebSocket**: en `gestionar_correccion_contacto_pendiente`
      (`agent.py`), la rama de cancelación leía `self.bizum_pendiente` en vez de
      `self.correccion_contacto_pendiente`, que ahí todavía es `None`.
      Reproducido: *"Haz un bizum a Maria de 20 euros"* → *"¿Querías decir María López?"* → *"no"*
      → `TypeError` → conexión muerta → historial perdido sin aviso. **Corregido y verificado.**
- [x] **Incoherencia en el mismo flujo**: al aceptar la sugerencia de contacto se preguntaba
      "¿Confirmas el envío?", pero cualquier "sí" caía en `gestionar_bizum_pendiente`, que solo
      acepta cancelar y responde "introduce tu PIN". Ahora pide el PIN directamente y emite `pedir_pin`.
- [x] **Blindaje**: `procesar` es ahora un envoltorio con `try/except` sobre `_procesar`, y el
      handler del WS en `main.py` tiene su propia red de seguridad. Ninguna excepción puede cerrar
      la conexión. Al fallar, se limpian `bizum_pendiente` y `correccion_contacto_pendiente`:
      una operación de dinero a medias es peor que volver a empezarla.
- [x] **`es_consulta_saldo` reescrito**. Antes hacía `substring` sobre expresiones genéricas y
      secuestraba consultas de análisis. Ahora exige un disparador **y** que todas las palabras del
      mensaje estén en una lista blanca: cualquier periodo, categoría o verbo extra descarta el atajo.
      Diseñado asimétrico a propósito — un falso negativo solo cuesta latencia (el LLM tiene la
      herramienta), un falso positivo da una respuesta incorrecta. Verificado con 18 casos:
      - ahora van al LLM: "evolución de mi saldo este año", "cuál era mi saldo el mes pasado",
        "cuánto me queda por pagar del alquiler", "cuánto tengo gastado en gasolina"
      - siguen usando el atajo: "¿cuál es mi saldo?", "cuánto dinero tengo", "dime mi saldo"…
- [x] **PIN con 3 intentos** (`INTENTOS_PIN` en `config.py`) y contador que se reinicia en cada
      Bizum nuevo vía `dejar_bizum_pendiente()`. El PIN sale del código del agente a `config.py`
      (`PIN_BIZUM`, sobreescribible por entorno) para que nunca pueda acercarse al prompt.
- [x] **Timeout de 5 min → 30 min** (`TIMEOUT_WS_SEGUNDOS`), y el frontend avisa con una nota
      visible cuando la sesión se reinicia, en vez de perder la memoria en silencio.
- [x] `pedir_pin` ahora llama a `clearPin()`: antes solo vaciaba el display, no la variable en memoria.

---

# 🟠 FASE 1 — Medir antes de tocar — **EN CURSO**

- [x] **`tests/preguntas.jsonl`: 34 preguntas** cubriendo los tres tipos que nombra el reto y algo más:
      gastos (9), comparativas (6), suscripciones (5), seguimiento (4), bizum (3), saldo (2),
      ingresos (2), ambiguas (1), límites (2).
- [x] **`tests/evaluar.py`**: lanza cada pregunta contra el agente real y mide **tres cosas
      independientes**, porque fallan por motivos distintos:
      1. **Elección de herramienta** — preguntar por suscripciones y que se ponga a escribir SQL
         es un fallo aunque el número salga bien. Se registra con un espía sobre `ejecutar_tool`,
         así se ven también los atajos del backend, que no pasan por el historial del LLM.
      2. **Precisión de ejecución del SQL** — no se compara el *texto* de la consulta (hay muchas
         consultas correctas distintas): se ejecutan la del agente y una de referencia escrita a
         mano, y se comparan los **valores**. Es la métrica estándar en text-to-SQL y es la única
         que no penaliza el estilo.
      3. **Respuesta final** — que el SQL sea correcto no garantiza que el modelo traslade bien la
         cifra. Se comprueba que los valores aparezcan en el texto, con formato español.
- [x] Los valores esperados **se recalculan en cada ejecución** desde la BD, así que no caducan al
      regenerar (`random.seed(42)` la hace reproducible).
- [x] **Puntúan dos cosas: herramienta y respuesta final.** La comparación del SQL contra una
      consulta de referencia se mide pero **no puntúa**: es diagnóstico. El modelo puede elegir una
      interpretación distinta y defendible (otro periodo, otra forma de calcular una media) y
      divergir de la referencia sin estar equivocado. Lo que cuenta es lo que oye el usuario.

### Resultados (qwen3:8b, 34 preguntas)

| | 1ª vuelta | 2ª | 3ª |
|---|---|---|---|
| Elección de herramienta | 93,9 % | 97,0 % | **97,0 %** |
| **Respuesta final correcta** | 78,1 % | 87,5 % | **93,8 %** |
| Comparativas | 4/6 | 6/6 | **6/6** |
| Gastos | 7/9 | 8/9 | **9/9** |
| Latencia mediana | 3,0 s | 3,2 s | **3,2 s** |

De 78,1 % a 93,8 % **sin cambiar el modelo ni la latencia**, solo corrigiendo lo que la medición
señaló. Esta tabla es el corazón de la sección de precisión de la memoria.

**El fallo más grave lo encontró la medición, no la vista**: a *"¿he gastado más este año que el
año pasado?"* generaba `strftime('%Y-%m')` en vez de `'%Y'`, comparaba **este mes contra el mes
pasado** y lo presentaba como años. Respondía *"has gastado más este año"* cuando la verdad era
17.870 € frente a 26.643 €: respuesta invertida, dicha con total seguridad. Arreglado con un
few-shot de años. En una demo habría sonado perfectamente creíble.

### 🔴 Hallazgo principal: el modelo no encadena herramientas

En las 34 preguntas, el agente **solo encadena una segunda herramienta dentro del mismo turno en 2
casos** (los dos gráficos). Nunca reconsulta tras ver un resultado. Consecuencia práctica:

> **Ninguna regla del prompt del tipo "mira el resultado y reacciona" puede funcionar.**

Explica a la vez el fallo de los gráficos y el de los resultados vacíos: son el mismo problema, y
la solución tiene que ser arquitectónica (llamada dedicada de tarea única), no de prompt.
Probado y revertido: reactivar el razonamiento tras un resultado vacío sube la latencia de 3,7 s a
7,5 s y el modelo sigue sin reconsultar.

Bug encontrado de paso: una consulta **agregada** sin resultados no devuelve cero filas, devuelve
**una fila llena de NULL** (`SELECT SUM(x) FROM t WHERE false` → `[[None]]`), así que detectar
"vacío" con `num_filas == 0` no detecta ninguna agregación.

- [ ] **Fallos asumidos** (decisión consciente a favor de la latencia): *"¿cuánto cobro de
      nómina?"* (se cobra el día 28; si hoy es 27 filtra por mes actual y dice que no hay nada),
      preguntas sin periodo, y el saldo de otra persona.

---

# ✅ FASE 2 — Los 30 puntos de precisión — **HECHA** (18 ago)

- [x] **`backend/analitica.py` + tool `analizar_suscripciones`**: detección determinista de
      pagos recurrentes en Python. **Hallazgo clave**: el importe no discrimina (el recibo de la
      luz varía tanto como un repostaje, CV 0,16 vs 0,25); lo que discrimina es la **regularidad
      de los intervalos** entre cargos:
      | | irregularidad |
      |---|---|
      | Netflix, Spotify, Digi, Gimnasio, Alquiler, Endesa | 0,03 |
      | Emasagra (bimestral) | 0,01 |
      | El más regular de los NO recurrentes | 0,12 |
      | Supermercados, gasolineras, restaurantes | 0,25 – 3,34 |
      Segunda condición necesaria: con menos de 3 intervalos la regularidad no prueba nada (con
      dos cargos hay UN intervalo y su desviación típica es cero por definición), así que ahí se
      exige que el importe sea idéntico. Eso deja pasar la póliza anual y descarta coincidencias.
      Calcula cadencia, coste mensual equivalente, subidas de precio escalonadas y si sigue activo.
- [x] **Validado contra 500 semillas distintas** (`tests/test_recurrencia.py`), comparando con la
      verdad conocida por construcción en `seed.py`: **0 falsos negativos, 1 falso positivo
      (0,2 % de los históricos), 0 cadencias mal clasificadas, seguro anual detectado 500/500**.
      Los umbrales salieron de esa medición, no de mirar una sola BD. Las dos decisiones que más
      pesaron: bajar la irregularidad de 0,10 a 0,06 (−7 FP, 0 FN nuevos) y **ampliar `seed.py` de
      15 a 24 meses**, que por sí solo llevó los falsos positivos de 23 a 0 — con más histórico,
      los comercios aleatorios acumulan cargos suficientes para que su irregularidad se note.
      Material de primera para la memoria: es una decisión de diseño medida, no una corazonada.
- [x] **`seed.py` ampliado a 24 meses** (`INICIO = hoy − 730 días`). Efectos: el seguro del coche
      ya tiene dos renovaciones y se detecta como pago anual (34,88 €/mes equivalente), se
      habilitan las comparativas interanuales, y desaparecen los falsos positivos. BD regenerada:
      1.020 movimientos entre 2024-08-01 y hoy.
- [x] **Few-shots de SQL** en el system prompt: comparativa mes actual vs anterior en una sola
      consulta, evolución mensual agrupada, semana en curso, y LIKE con comodines a ambos lados.
- [x] **Contexto**: `MAX_FILAS` 200 → 50, recorte del historial a 24 mensajes
      (`recortar_historial()`, corta siempre en frontera de turno para no dejar mensajes `tool`
      huérfanos que la API rechaza) y tope de 4.000 caracteres por resultado de herramienta.

**Verificado en vivo contra Ollama**, con las cifras contrastadas contra SQL directo:

| Pregunta | Tool | Resultado | Tiempo |
|---|---|---|---|
| "¿A qué estoy suscrito?" | `analizar_suscripciones` | 8 pagos, 842,10 €/mes ✅ | 5,8 s |
| "¿Cuánto me cuestan al mes los pagos fijos?" | `analizar_suscripciones` | 842,10 € ✅ | 2,9 s |
| "¿Cuánto he gastado este mes vs el pasado?" | `consultar_movimientos` | 1.752,89 € vs 2.034,05 € ✅ | 4,9 s |
| "¿Cuánto le he enviado por bizum a María?" | `consultar_movimientos` | 675,29 € ✅ | 2,7 s |

- [x] **Subida de precio en los datos**: Netflix pasa de 13,99 € a 15,99 € a mitad del histórico
      (`SUBIDA_NETFLIX` en `seed.py`, ~1 año a cada precio). Sin esto la detección de cambios de
      precio no tenía nada sobre lo que dispararse. El test lo comprueba en las 100 semillas.
- [x] **La respuesta de la tool se entrega ya resuelta, no en crudo**. Con la lista plana y un
      `cambio_precio: null` en siete de ocho entradas, el modelo agregaba mal: *"ninguna
      suscripción ha subido de precio. Sin embargo, Netflix ha subido de 13,99 a 15,99 €"* — se
      contradecía en la misma frase. Ahora devuelve `suscripciones` y `recibos_fijos` separados,
      más `hay_subidas_de_precio` y `subidas_de_precio`. Quitarle el trabajo de agregar es lo que
      lo arregla, y a coste cero de latencia. Misma lección que con los gráficos: si el backend
      puede resolverlo, no se lo pidas a un 8B.
      Verificado: *"¿Me ha subido de precio alguna suscripción?"* → **"Sí, Netflix. Antes costaba
      13,99 € y ahora 15,99 €, un incremento de 2,00 € al mes"** en 4,0 s.

### Cosmético, sin prisa
- [ ] Al enumerar, el modelo sigue metiendo los recibos en la frase de "estás suscrito a…"
      (*"estás suscrito a el gimnasio, Netflix, Spotify, alquiler, luz…"*). Cifras y orden son
      correctos, y el payload ya viene separado. Se descartó meter un ejemplo de redacción con
      cifras concretas en el prompt: un modelo de 8B puede copiar esas cifras literales cuando los
      datos sean otros, y eso convierte un fallo de estilo en un error factual.

### 🔴 Pendiente: fiabilidad de los gráficos (20 pts) — DIAGNOSTICADO, NO RESUELTO

Medido sobre 6 preguntas que según los criterios del system prompt debían acabar en gráfico:
**solo 2 lo hacen**. Falla justo en las que el modelo puede resumir en una frase ("has gastado X
frente a Y"): ve el resultado, lo da por respondido y se salta la regla, que está a ~2.000 tokens
de distancia en el system prompt. Las que sí funcionan son las que llevan lenguaje visual
explícito ("muéstrame", "cómo se reparten").

Se intentó recordárselo con un aviso generado por el backend cuando el resultado tiene 2+ valores
comparables (sin elegir el tipo de gráfico: esa decisión debe seguir siendo del LLM porque es lo
que puntúa como "sin plantillas"). **Dónde se coloca el aviso importa muchísimo**, medido con
repeticiones del mismo turno:

| Variante | Gráficos |
|---|---|
| Sin aviso (control) | 0/3 — siempre responde con texto |
| Aviso dentro del propio resultado de la tool | 0/3 — **respuesta VACÍA** |
| Aviso como mensaje `user` aparte | 0/3 — **respuesta VACÍA** |
| Aviso como mensaje `system` aparte | 2/3 |

Y la causa de las respuestas vacías es `reasoning_effort: none`:

| | Gráficos | Vacías |
|---|---|---|
| `reasoning_effort=none` | 2/5 | 3 |
| Sin `reasoning_effort` (razonamiento activo) | 4/5 | 0 |

**Se implementó y se revirtió**: activar el razonamiento solo en esa iteración subía los gráficos
de 2/6 a 3/6 pero disparaba la latencia de 4-24 s a 17-34 s, pagándola incluso cuando no acababa
en gráfico. Mal cambio: 15 pts de agilidad por +1 gráfico de 6. **Decisión: mantener baja latencia
y gráficos regulares por ahora.**

- [ ] Arreglo bueno pendiente para la Fase 3: **llamada dedicada solo para el gráfico**. Tras un
      resultado graficable, hacer UNA petición aparte cuya única tarea sea devolver la spec
      Vega-Lite, con un prompt mínimo y solo los datos. Un modelo de 8B es mucho más fiable en una
      tarea única que decidiendo entre responder y encadenar herramienta. Se puede lanzar en
      paralelo con la redacción de la respuesta, así que no añade latencia percibida.
- [ ] **Un gráfico llegó con `mark` a nulo** (13 datos, sin marca). `tools.py` solo valida que
      haya `data.values` — a propósito, para admitir `layer`/`concat` — así que una spec sin marca
      pasa el filtro y luego falla en `vega-embed`. Revisar y dar mensaje claro al modelo.
- [ ] **Ninguna suscripción cambia de precio en los datos**, así que la rama de detección de
      subidas está implementada pero no se puede demostrar. Una subida de Netflix a mitad del
      histórico (una línea en `seed.py`) daría un momento muy bueno de vídeo.
      Ollama trunca por delante, se pierde el system prompt y la calidad se cae. Bajar el límite
      y recortar/resumir el historial.

---

# 🟠 FASE 3 — GenUI y agilidad (1-2 semanas)

### Tablas (refuerza los 10 pts de lógica visual)
- [ ] **Tool `mostrar_tabla`** + criterio gráfico-vs-tabla en el prompt.
      Argumento: elegir entre 4 marcas de Vega-Lite es una decisión pobre; en cuanto la IA puede
      elegir *tabla o gráfico*, el "por qué esta representación" pasa a ser lógica visual real
      — y es literalmente lo que pide el enunciado.

### Agilidad (15 pts)
- [ ] **Streaming percibido**: `agent.py` bufferiza todo el texto y lo emite al final de la
      iteración (para no mostrar texto previo a las tools). Emitir deltas en cuanto llegue texto
      y cortar/limpiar si aparece un tool call en el mismo turno.
- [ ] **Indicador durante las tools**: evento `{"type": "tool_inicio", "nombre": ...}` antes de
      `ejecutar_tool`, y spinner con estado en el frontend ("Generando gráfico…", "Consultando
      movimientos…"). Hoy solo hay el punto parpadeante de la burbuja vacía.
- [ ] **TTS por frases**: trocear por puntuación y hablar cada frase según llega.

### Gráficos (10 pts)
- [ ] Fallback: si la spec llega sin datos o corrupta, reconstruir `data.values` desde el último
      resultado de `consultar_movimientos` en vez de fallar.
- [ ] Probar variedad: línea (evolución), barras (ranking), donut (distribución).

### Operaciones (10 pts)
- [ ] Ampliar regex de `extraer_peticion_bizum`. Verificado que fallan hoy:
      "bizum de 20 euros para María", "págale 15 euros a Ana por bizum".
      (Cuando falla no es fatal: cae al LLM, que tiene la tool — pero es más lento y menos fiable.)
- [ ] Probar el flujo completo por voz: petición → sugerencia de contacto → PIN → saldo actualizado.

### Extra diferenciador si da tiempo
- [ ] **Proyección de gasto a fin de mes** / alerta de desviación vs. media histórica
      ("Analítica Predictiva" aparece en el enunciado).

---

# 🟡 FASE 4 — Entregables (última semana)

- [ ] **Regenerar `banco.db`** (`python -m backend.seed`): los datos se generan relativos a *hoy*.
      Con una BD vieja, "este mes" sale vacío. Comprobado: el último movimiento es del 12 de agosto.
- [ ] **Vídeo demo** (5 pts): voz + consulta compleja + SQL visible + seguimiento contextual +
      gráfico y tabla + suscripciones + Bizum con PIN.
- [ ] **Memoria técnica** (5 pts): arquitectura, diseño del agente y tools, text-to-SQL
      (seguridad + autocorrección + tabla de precisión de la Fase 1), motor visual y criterio de
      representación, IA responsable, latencias medidas.

---

# 🟡 Pulido y deuda técnica

- [ ] `requirements.txt`: `anthropic` y `ollama` no se importan en ningún sitio, quitar.
      Ojo: `openai` SÍ es necesario aunque el proveedor sea Ollama — es el cliente del endpoint
      OpenAI-compatible (`http://localhost:11434/v1`).
- [ ] `README.md` (ambos): siguen diciendo "pon tu ANTHROPIC_API_KEY". Actualizar a Ollama
      (`ollama pull qwen3:8b`, `./arrancar_ollama.sh`).
- [ ] **Fuga de conexiones SQLite**: `with conexion_lectura() as conn:` **no cierra** la conexión
      — el context manager de sqlite3 solo hace commit/rollback. Afecta a `database.py` y a todo
      `banking_api.py`. Cada consulta deja un descriptor abierto.
- [ ] Migrar `@app.on_event("startup")` (`main.py`) a `lifespan` (deprecado en FastAPI).
- [ ] Error visible en la UI si Ollama no está arrancado (hoy sale un error críptico).
- [ ] **Código muerto** en `index.html` (~línea 464): un bloque dentro de `if (Reconocedor)` lee
      `input.value` y hace `enviar()` en tiempo de carga de página, cuando el input siempre está
      vacío. Parece que debía ir dentro de `rec.onend`.
- [ ] `retoCajaRural.zip` está commiteado dentro del propio repo.
- [ ] Revisar la UI en móvil / ventana estrecha.

