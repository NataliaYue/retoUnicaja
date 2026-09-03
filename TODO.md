# TODO — Reto 2: Analítica Avanzada y Generative UI

**Cátedra IA Responsable en Finanzas** (Unicaja & UGR, con NVIDIA) · https://catedraiaunicaja.ugr.es/reto2.html

> El detalle de lo ya hecho (Fases 1 a 6: qué se decidió, por qué, y con qué medición) vive en el
> historial de git de este fichero y en los mensajes de commit. Aquí solo queda lo pendiente.

## Baremo oficial

| Criterio | Pts | Estado |
|---|---|---|
| Precisión de consultas | **30** | 97,0 % herramienta · 90,3 % respuesta |
| Conversación | 15 | **sin medir** |
| Agilidad | 15 | 3,3 s mediana hasta la voz |
| Operaciones | 10 | Bizum con PIN y límites validados |
| Lógica visual | 10 | la IA elige tabla o gráfico y lo justifica |
| Gráficos sin plantillas | 10 | refuerzo visual 24/24 |
| Vídeo | 5 | **sin empezar** |
| Memoria | 5 | **sin empezar** |

---

## Reglas operativas (no saltárselas)

- **Regenerar `banco.db` antes de cada tanda de evaluación y antes del vídeo**
  (`python -m backend.seed`). Los datos son relativos a *hoy*: en cuanto cambia el mes,
  "este mes" y "esta semana" devuelven `NULL` y esas preguntas dejan de puntuar.
  Ya invalidó una comparación entera sin avisar, porque el número que sale es plausible.
- **Nunca comparar configuraciones con una sola vuelta.** La varianza de qwen3:8b es de ±1 caso;
  una diferencia de 2 casos en un pase suelto es ruido. Usar `--repeticiones 3`.
- **Si Ollama se cae, todo falla en ~1,5 s** en vez de tardar segundos. Al relanzarlo con
  `./arrancar_ollama.sh`, comprobar con `curl -s localhost:11434/api/ps` que el contexto sigue en
  8192: sin las variables de entorno arranca en 4096 y la calidad cae sin que nada lo avise.
- **Cada regla del system prompt se dice UNA sola vez.** Repetirla le roba peso a las demás y
  cuesta latencia en cada turno, porque el prompt viaja entero en todas las llamadas.

## Los tres tests

```bash
cd banco-conversacional

# Flujo de Bizum: regex → contacto → PIN → envío. Sin LLM, determinista, ~10 s.
.venv/bin/python -m tests.test_bizum

# Detector de pagos recurrentes contra N semillas. Sin LLM, ~30 s.
.venv/bin/python -m tests.test_recurrencia 50

# Banco de precisión. NECESITA Ollama arrancado y la BD del día. ~13 min.
python -m backend.seed && .venv/bin/python -m tests.evaluar --repeticiones 3
```

Los tres devuelven código de salida 0 si todo pasa.

**Ojo con qué cubre cada uno.** `evaluar.py` NO toca el flujo de envío de Bizum (sus preguntas de
"bizum" son consultas sobre bizums pasados), así que para tocar ese código el que vale es
`test_bizum`. Y al revés: `test_bizum` no ve nada de precisión ni de gráficos.

---

# 🔵 Decisiones abiertas (para discutir más adelante)

## ¿Debe el LLM decidir CUÁNDO mostrar un gráfico?

Hoy **no lo decide**: está fijado. El reparto actual es este.

| | quién decide |
|---|---|
| **Cuándo** mostrar algo | **fijo** — `es_graficable()` en `graficos.py`, una regla determinista |
| **Qué**: tabla o gráfico | **el LLM** |
| **Cómo**: marca, ejes, columnas, título, y el porqué | **el LLM** |

La regla del "cuándo" es solo esto: ≥2 filas con alguna columna numérica → sí; 1 fila con ≥2
cifras (una comparación) → sí; lo demás → no. Y `analizar_suscripciones` siempre, porque devuelve
8 filas.

**Matiz importante**: el LLM sí influye en el cuándo, de forma indirecta, porque es él quien
escribe el SQL y el SQL determina la forma del resultado. Medido en `comp-gasolina-6-meses`: con
`SELECT SUM(...)` sale 1 fila y no hay visual; con `GROUP BY mes` salen 6 y sí lo hay. La misma
pregunta acababa o no en gráfico según cómo decidiera consultar. Por eso se arregló con una regla
de SQL, no tocando el gate.

### Por qué está así

Cuando el LLM decidía el cuándo, lo hacía **de forma inestable**: 3 aciertos de 18, y la
decisión se movía con cualquier edición del system prompt aunque no hablara de gráficos. El gate
determinista acierta 15/15 sobre las preguntas etiquetadas.

### El coste de cambiarlo, medido (3 vueltas, BD regenerada)

| | LLM decide el cuándo | backend decide (actual) |
|---|---|---|
| Respuesta final correcta | 86,0 % | **90,3 %** |
| Latencia hasta la voz (preguntas con visual) | **12,2 s** | **6,8 s** |
| Refuerzo visual cuando toca | 23/24 | **24/24** |
| Prompt | 2.912 tok | **2.396 tok** |

Con la herramienta expuesta el modelo la llama a veces (15 de 35 visuales) y, cuando lo hace, es
**en medio del turno**: el usuario espera el doble a oír la respuesta. Y aun así muestra menos.

### Qué habría que discutir

Los números dicen que el reparto actual es mejor. Lo que queda por decidir es **cómo se lee el
proyecto**: si "la IA invoca la herramienta de gráficos dentro de su bucle agéntico" pesa en la
valoración por encima de los 5,4 s de latencia y los 4 puntos de precisión.

Argumento a favor de dejarlo como está: la IA **conserva todas las decisiones de diseño visual**
—elige tabla o gráfico, la marca, los ejes y lo justifica—, y lo único que se le quita es una
decisión mecánica que no sabía tomar. Eso se defiende bien en la memoria como decisión de
arquitectura medida.

- [ ] **Decidirlo antes de grabar el vídeo.** El cambio es una sola variable:
      `VISUALES_AL_LLM = 1` en `config.py` (o `VISUALES_AL_LLM=1` como variable de entorno).
      El interruptor está puesto justamente para poder rehacer la medición y comparar.

# 🔴 Lo que más puntos deja sobre la mesa

### Conversación (15 pts) — sin medir

Solo lo cubren 4 preguntas de seguimiento de 34. Es la segunda partida más grande del baremo y
está a ojo: la misma situación en la que estaban los gráficos antes de instrumentarlos, y ahí la
medición acabó cambiando la arquitectura.

- [ ] Ampliar `preguntas.jsonl` con casos multiturno de verdad (3-4 turnos), referencias
      pronominales, cambio de tema y vuelta atrás. Comprobar que `recortar_historial()` no rompe
      referencias cuando corta.

### Entregables (10 pts) — sin empezar

Son los únicos puntos que dependen solo de ti, y no se improvisan el último día.

- [ ] **Vídeo demo** (5 pts): voz + consulta compleja + SQL visible + seguimiento contextual +
      gráfico y tabla + suscripciones + Bizum con PIN.
- [ ] **Memoria técnica** (5 pts): arquitectura, diseño del agente y tools, text-to-SQL
      (seguridad + autocorrección + tabla de precisión), motor visual y criterio de
      representación, IA responsable, latencias medidas.
      El material está en el historial de git de este fichero y en los mensajes de commit.

---

# 🟠 Mejoras con retorno claro

### Agilidad (15 pts)
- [ ] **Streaming percibido**: `agent.py` bufferiza todo el texto y lo emite al final de la
      iteración (para no mostrar texto previo a las tools). Emitir deltas en cuanto llegue texto
      y cortar/limpiar si aparece un tool call en el mismo turno.

- [ x] **Indicador durante las tools**: evento `{"type": "tool_inicio", "nombre": ...}` antes de
      `ejecutar_tool`, y spinner con estado en el frontend ("Consultando movimientos…"). Hoy solo
      hay el punto parpadeante de la burbuja vacía.

- [ ] **TTS por frases**: trocear por puntuación y hablar cada frase según llega.

### Operaciones (10 pts)
- [x] Ampliar regex de `extraer_peticion_bizum`. Verificado que fallan hoy:
      "bizum de 20 euros para María", "págale 15 euros a Ana por bizum".
      Cuando falla no es fatal —cae al LLM, que tiene la tool— pero es más lento y menos fiable.
- [ ] Probar el flujo completo por voz: petición → sugerencia de contacto → PIN → saldo actualizado.

### Analítica predictiva
- [ ] **Proyección de gasto a fin de mes** / alerta de desviación vs. media histórica.
      "Analítica Predictiva" aparece literalmente en el enunciado y el sistema es 100 %
      retrospectivo. Encaja directa sobre el motor visual que ya existe.

---

# 🟡 Deuda técnica

- [x] **El flujo Bizum estaba escrito dos veces** y las copias ya habían divergido: la del backend
      no validaba el importe. Ahora `iniciar_bizum()` es el único sitio donde vive; la diferencia
      real entre las dos vías (registrar el `tool_result` en el historial, que sin él la API
      rechaza la petición siguiente) queda aislada en un parámetro.
      `agent.py` 1.116 → 1.059 líneas · `_procesar()` ~410 → 276.
      **Antes del refactor se escribió `tests/test_bizum.py`**, porque `evaluar.py` NO cubre ese
      camino: sus cuatro preguntas de "bizum" son consultas sobre bizums pasados. Se podía romper
      el envío entero y el banco habría seguido dando 97 %. 32/32 antes y después, y el banco
      completo sin mover una cifra (96/108 exactos en las dos vueltas, ningún caso peor).
- [x] **Fuga de conexiones SQLite**: `with conexion_lectura() as conn:` **no cierra** la conexión
      — el context manager de sqlite3 solo hace commit/rollback. Afecta a `database.py` y a todo
      `banking_api.py`. Cada consulta deja un descriptor abierto.
- [x] `requirements.txt`: `anthropic` y `ollama` no se importan en ningún sitio, quitar.
      Ojo: `openai` SÍ es necesario aunque el proveedor sea Ollama — es el cliente del endpoint
      OpenAI-compatible (`http://localhost:11434/v1`).
- [x] `README.md` (ambos): arranque actualizado a Ollama, sin claves de API, y "24 meses" en vez
      de "~15". El de `banco-conversacional/` estaba entero sin tocar y además decía
      "LLM (Claude)" en el diagrama de arquitectura.
- [x] Migrar `@app.on_event("startup")` a `lifespan`. Verificado con `TestClient`: la comprobación
      de que existe `banco.db` sigue ejecutándose al arrancar.
- [x] Error visible en la UI si Ollama no está arrancado. `explicar_error()` en `agent.py` traduce
      el `APIConnectionError` a *"no puedo contactar con el modelo en http://localhost:11434/v1,
      comprueba que Ollama esté arrancado (./arrancar_ollama.sh)"*, en vez del escueto
      "Connection error.". Es el fallo más probable y más desconcertante en una demo, porque todo
      lo demás sigue funcionando: la página carga, el saldo se ve, y solo el chat deja de responder.
- [x] **Código muerto** en `index.html`: nunca se ejecutaba, porque `input.value` está vacío al
      cargar la página. Movido a `rec.onend`, donde sí sirve: si el reconocimiento de voz termina
      sin resultado final —pasa al cortar por silencio— el texto se quedaba en el input y la orden
      se perdía. No duplica envíos, porque `enviar()` vacía el input.

---

# ⚪ Fallos asumidos (decisión consciente, no olvidos)

- *"¿cuánto cobro de nómina?"*: se cobra el día 28; si hoy es 27, filtra por el mes actual y dice
  que no hay nada. Se asume a favor de la latencia.
- Preguntas sin periodo: se responde por el mes actual y se dice explícitamente.
- Al enumerar suscripciones, el modelo mete los recibos en la frase de "estás suscrito a…".
  Cifras y orden son correctos y el payload ya viene separado. Se descartó meter un ejemplo de
  redacción con cifras concretas en el prompt: un 8B puede copiarlas literalmente cuando los datos
  sean otros, y eso convierte un fallo de estilo en un error factual. La tabla lo mitiga: ahora la
  lista se ve en pantalla, no solo se oye.
