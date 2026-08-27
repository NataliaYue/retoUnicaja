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

- [x] Arreglo bueno pendiente para la Fase 3: **llamada dedicada solo para el gráfico**. Tras un
      resultado graficable, hacer UNA petición aparte cuya única tarea sea devolver la spec
      Vega-Lite, con un prompt mínimo y solo los datos. Un modelo de 8B es mucho más fiable en una
      tarea única que decidiendo entre responder y encadenar herramienta. ~~Se puede lanzar en
      paralelo con la redacción de la respuesta, así que no añade latencia percibida.~~
      **HECHO el 27 ago** (`backend/graficos.py`), con dos correcciones al plan — ver Fase 5.
      El paralelo **no era viable**: `qwen3:8b` ocupa 6,6 GB de los 8 GB de la 4060 y no cabe un
      segundo slot de KV cache, así que `OLLAMA_NUM_PARALLEL=2` no entra y Ollama serializa igual.
      Se resolvió por orden de eventos, no por paralelismo.
- [x] **Un gráfico llegó con `mark` a nulo** (13 datos, sin marca). `tools.py` solo valida que
      haya `data.values` — a propósito, para admitir `layer`/`concat` — así que una spec sin marca
      pasa el filtro y luego falla en `vega-embed`. **Cerrado por partida doble el 27 ago**:
      `evaluar.py` lo detecta ahora solo (métrica `spec_ok`, y se reprodujo en la primera
      ejecución sin ir a buscarlo), y la llamada dedicada rechaza toda spec sin `mark` o sin
      `encoding` antes de emitirla.

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

- [x] **Regenerar `banco.db`** (`python -m backend.seed`): los datos se generan relativos a *hoy*.
      Con una BD vieja, "este mes" sale vacío. **Hecha**: 1.035 movimientos entre 2024-08-01 y
      2026-08-26. Ojo, **caduca sola**: hay que repetirlo antes del vídeo y antes de cada tanda
      de evaluación, porque "esta semana" se vacía en cuanto pasan unos días.
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



---

# ✅ FASE 5 — Bug del PIN, medición de gráficos y motor visual — **HECHA** (27 ago)

Tres cambios. El hilo conductor: **casi todo lo que se arregló lo encontró la medición, no la
vista**, y dos de las tres cosas que parecían mejoras resultaron no serlo al medirlas con
repeticiones.

## 5.1 — El PIN se pedía para operaciones que ya se sabía que iban a fallar 🔴

El importe de un Bizum no se comprobaba hasta `api_enviar_bizum`, es decir, **después** de que el
usuario tecleara su clave. Un envío de 2.000 € recorría el flujo entero — *"vas a enviar 2.000,00 €
a María López, introduce tu PIN"* → teclado numérico → PIN → *"el importe debe estar entre 0,50 € y
1.000 €"*. Pedir una clave para algo que no se va a ejecutar es malo en una demo y peor como diseño
de seguridad.

Los dos caminos habían divergido, que es el síntoma de la duplicación del flujo Bizum:

| | `cantidad <= 0` | `cantidad > 1.000` |
|---|---|---|
| Vía regex (`preparar_bizum_desde_backend`) | ❌ no validaba | ❌ no validaba |
| Vía LLM (tool call en `_procesar`) | ✅ preguntaba el importe | ❌ no validaba |

- [x] `validar_importe_bizum()` en `agent.py`, llamada por **los dos caminos** antes de
      `dejar_bizum_pendiente()`.
- [x] Los límites viven en `config.py` (`BIZUM_MIN`, `BIZUM_MAX`, `BIZUM_LIMITE_DIARIO`) para que
      agente y API no puedan decir cosas distintas. La API **los sigue comprobando por su cuenta**:
      es la última palabra antes de mover dinero y no debe fiarse de quien la llama.
- [x] Verificado: 2.000 € y 5.000 € se rechazan sin pedir PIN, 0 € pregunta cuánto, 20 € sigue
      pidiendo PIN. Límites probados en 0,49 / 0,50 / 1.000,00 / 1.000,01.

## 5.2 — Los 20 pts de gráficos ya se miden solos

Se medían con rigor los 30 pts de precisión y los 20 de gráficos se comprobaban a ojo. Los eventos
ya se recogían en `evaluar.py`, solo no se leían.

- [x] **`grafico_ok`**, con las preguntas etiquetadas `espera_grafico`. Puntúa **en los dos
      sentidos**: no pintar donde hay varios valores comparables es un fallo, y pintar donde la
      respuesta es un único dato también, porque el criterio del propio system prompt lo prohíbe.
- [x] **`spec_ok`**, aparte, porque es un fallo de otra cosa: `grafico_ok` mide al modelo
      **decidiendo**, `spec_ok` al modelo **redactando**. Cazó el bug del `mark` a nulo en la
      primera ejecución, sin ir a buscarlo (es intermitente, ~1 de 6).
- [x] **Corregidas dos etiquetas mal puestas**: `gasto-categoria-top` y `comp-mes-mas-gasto`
      devuelven **una fila con un solo número**, así que no pintarlas era lo correcto y se estaban
      contando como fallo. Se puede defender lo contrario (pintar el desglose entero es mejor
      GenUI), y por eso se dejan **sin puntuar** en vez de forzar una dirección, igual que se hace
      con la comparación de SQL. Quedan 6 preguntas defendibles.
- [x] **Latencia partida en dos**: *hasta la voz* (cuándo se emite `fin_respuesta`, que es lo que
      dispara el TTS y por tanto lo que el usuario experimenta) y *total*. Medir solo el total
      penalizaría trabajo que ya no bloquea la respuesta.

### 🔴 Hallazgo: la decisión de pintar no la gobierna el prompt

Medido sobre **4 variantes del system prompt × 3 vueltas de las 34 preguntas**:

| pregunta (de las 6 defendibles) | viejo | v3 | v4 | v5 |
|---|---|---|---|---|
| gasto-top-comercios | 0/3 | 2/3 | 0/3 | 0/3 |
| comp-mes-vs-anterior | 0/3 | 0/3 | 0/3 | 0/3 |
| comp-super-vs-restaurantes | 0/3 | 2/3 | 2/3 | 0/3 |
| comp-gasolina-6-meses | 0/3 | 3/3 | 3/3 | 0/3 |
| comp-anio-vs-anterior | 0/3 | 0/3 | 0/3 | 0/3 |
| comp-evolucion-mensual | 3/3 | 3/3 | 3/3 | 3/3 |

Dentro de cada versión el comportamiento es casi determinista (0 o 3 de 3), pero **entre versiones
la moneda se vuelve a tirar, y basta con editar dos líneas que ni siquiera hablan de gráficos**
(v4 → v5 solo cambia las recetas de fechas y una regla de `consultar_saldo`, y los gráficos caen
de 11/24 a 3/24). Solo pinta siempre la que lleva lenguaje visual explícito; dos no pintan jamás.

Esto es más fuerte que el diagnóstico anterior ("la regla está a ~2.000 tokens de distancia"): la
decisión **no depende de la distancia ni de la redacción**. Confirma que hacía falta el arreglo
arquitectónico, y descarta seguir tocando el prompt para esto.

## 5.3 — System prompt reescrito: 3.364 → 2.760 tokens

Del 41 % del contexto al 34 %. Tenía una **contradicción directa** sobre `enviar_bizum` (poner
`cantidad: 0` en una línea, omitir el parámetro seis líneas más abajo), la regla de no-Markdown
**ocho veces**, la de gastos-en-positivo repetida, y `ESQUEMA_BD` incrustado a mitad de una frase
por un salto de línea que faltaba.

Verificado con un script que **ninguna de las 18 reglas ganadas midiendo se perdió**.

Resultado con 3 vueltas (102 ejecuciones por configuración):

| | viejo 3.364 t | v5 2.760 t |
|---|---|---|
| Elección de herramienta | 97,0 % | **97,0 %** |
| Respuesta final | 91,7 % | **90,6 %** |
| Latencia mediana | 3,2 s | **3,0 s** |

Empate en precisión (un caso de 96, dentro del ±1 de varianza) con un 18 % menos de tokens en cada
llamada. **Mejora modesta y segura, no la que parecía al principio.**

### Tres regresiones que encontró la medición, no la vista

Las tres las **causó la propia reescritura** y están corregidas:

1. **Fechas adyacentes.** Poner *"este año"* y *"último año"* en la misma línea separados por un
   `·` hizo que el modelo filtrara el año natural con `date('now','-1 year')`: respondía 6.687 €
   donde eran 4.521 €. Tres casos, 0/3 los tres. **Comprimir juntó dos recetas casi idénticas y
   creó una ambigüedad que el prompt largo no tenía.** Al corregirlo me pasé de frenada y eliminé
   la receta de "últimos N meses", lo que rompió otra pregunta (3/3 → 0/3): la solución era
   **contrastar** las dos, no quitar una.
2. **Ejemplos sesgados.** Todos los ejemplos usaban `CASE` sobre fechas, así que al comparar dos
   *categorías* el modelo copiaba el patrón y devolvía el mismo número dos veces (*"supermercado y
   restaurantes son iguales: 6.203,17 €"*). Añadido el ejemplo del `CASE` sobre `categoria`.
3. **Presión del gráfico.** Forzarlo con *"llámalo ANTES de redactar nada"* producía **5 respuestas
   vacías de 102** (0 en el viejo), siempre tras un SQL correcto y **sin llegar a pintar**. Es
   exactamente el patrón "mira el resultado y reacciona" que la Fase 1 ya había demostrado que no
   funciona. Al soltarlo desaparecieron.

### ⚠️ Lección de método

Las dos primeras iteraciones se hicieron sobre **pases sueltos** y se interpretaron las
regresiones como ruido. Eran señal: **la varianza real es de ±1 caso** y la caída era de 4. Con
`--repeticiones 3` se ve a la primera. **No comparar configuraciones con una sola vuelta.**

## 5.4 — Motor visual: `backend/graficos.py` 🆕

El reparto de responsabilidades es lo que hace que funcione:

| decisión | quién | ¿puntúa? |
|---|---|---|
| ¿**hay** algo que pintar? | backend, determinista | no — es la forma del resultado |
| ¿**qué** marca? ¿qué encoding? ¿qué título? | **el LLM** | sí — "sin plantillas", 10 pts |
| ¿**por qué** esa representación? | **el LLM** (`razonamiento`) | sí — lógica visual, 10 pts |

- [x] **`es_graficable()`** — el gate. Dos formas dan gráfico: varias filas con alguna columna
      numérica (serie o ranking), o **una fila con dos o más cifras** (comparación). Esa segunda
      rama es la que el prompt nunca alcanzaba: *"¿cuánto he gastado este mes comparado con el
      pasado?"* devuelve una sola fila con dos columnas. **Validado 15/15** contra las preguntas
      etiquetadas antes de escribir una línea del resto.
- [x] **`preparar_datos()`** — pivota de ancho a largo. Vega-Lite no sabe poner nombres de columna
      en un eje sin un `transform`/`fold`, así que `{este_mes: 2036, mes_pasado: 2399}` se
      convierte en dos filas `{serie, valor}`. Con eso al modelo solo le queda elegir la marca.
- [x] **`generar_spec()`** — la llamada dedicada. **Le pide solo `title`, `mark`, `encoding` y
      `razonamiento`: los datos los inyecta el backend**, que ya los tiene. Eso quita ~700 tokens
      de generación por gráfico y elimina de raíz que se invente cifras. Nunca lanza: un gráfico
      que falla no puede tumbar la respuesta.
- [x] **`mostrar_grafico` se queda expuesta al LLM.** Si el modelo decide pintar, el gráfico es
      100 % suyo; el backend solo actúa si el turno terminó sin gráfico. Quitarla habría eliminado
      comportamiento agéntico que **sí funciona** (3/3 en *"muéstrame la evolución"*, en las cuatro
      versiones del prompt) solo por simplificar.
- [x] **La llamada va DESPUÉS de `fin_respuesta`**, y ese detalle es el que decide si el cambio es
      bueno o malo. `fin_respuesta` es lo que dispara el TTS en el frontend, así que ponerla antes
      retrasaría la respuesta hablada los ~7 s de la spec — la misma penalización de agilidad por
      la que se revirtió el intento de la Fase 2. Ahora el usuario oye la respuesta de inmediato y
      el gráfico aparece mientras la escucha.
- [x] **Frontend**: un gráfico que llega tras `fin_respuesta` ya no crea una burbuja vacía, que se
      habría quedado con el punto de "escribiendo" parpadeando para siempre
      (`.msg.asistente:empty::after`). Flag `turnoActivo`.

### Resultado (3 vueltas, 102 ejecuciones por configuración)

| | antes de hoy | con motor visual |
|---|---|---|
| Elección de herramienta | 97,0 % | **97,0 %** |
| Respuesta final correcta | 91,7 % (88/96) | **91,7 % (88/96)** |
| **Gráfico cuando toca** | 16,7 % (3/18) | **83,3 % (15/18)** |
| Specs que llegan a pintarse | 3/3 | **18/18** |
| Latencia hasta la voz (mediana) | 3,2 s | **3,2 s** |
| Respuestas vacías | 0 | **0** |

Por pregunta, veces que pinta de 3:

| | antes | ahora |
|---|---|---|
| gasto-top-comercios | 0 | **3** |
| comp-mes-vs-anterior | 0 | **3** |
| comp-super-vs-restaurantes | 0 | **3** |
| comp-anio-vs-anterior | 0 | **3** |
| comp-evolucion-mensual | 3 | 3 |
| comp-gasolina-6-meses | 0 | 0 ← fallo de SQL, no del motor |

**Cuatro de las cinco preguntas que no pintaban nunca ahora pintan 3/3**, con la precisión intacta
(88/96 exactos en ambos, no una aproximación) y **sin coste de agilidad**: la mediana hasta la voz
no se mueve porque el gráfico va después de `fin_respuesta`. Es el mismo objetivo del intento de la
Fase 2, que se revirtió por subir la latencia de 4-24 s a 17-34 s; la diferencia está en dónde se
coloca la llamada, no en cuánto se insiste en el prompt.

---

# 🔵 Pendiente después de la Fase 5

- [ ] **`compara ... en los últimos seis meses` no agrupa por mes.** El modelo escribe un `SUM`
      total, así que el resultado es un único dato y el gate hace bien en no pintarlo. El fallo es
      de SQL: *"compara"* debería implicar desglose. Se arregla en el prompt, no en el motor.
- [ ] **Deuda que sigue viva**: `agent.py` va por ~1.000 líneas y el flujo Bizum sigue escrito dos
      veces (`preparar_bizum_desde_backend` y la rama del tool call). La Fase 5.1 tapó la
      divergencia, pero no la causa. Extraer un `iniciar_bizum()` común, o un `bizum.py`.
- [ ] **Tablas (`mostrar_tabla`)** y **proyección de gasto**: siguen pendientes de la Fase 3, y
      ahora hay con qué medirlas.
