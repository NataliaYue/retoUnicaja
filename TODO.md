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

# 🟠 FASE 1 — Medir antes de tocar (3-4 días)

Sin esto todo lo demás son corazonadas, y no hay nada que contar en la memoria.

- [ ] `tests/preguntas.jsonl`: ~30 preguntas cubriendo los tres tipos que nombra el reto
      (**gastos, comparativas, suscripciones**) + ambiguas + de seguimiento ("¿y esta semana?").
- [ ] Runner que las lanza contra el agente y mide **acierto del SQL** y **latencia**
      (primer token, respuesta completa, con y sin gráfico).
- [ ] Tabla de resultados → material directo para la memoria y detección de regresiones.

---

# 🟠 FASE 2 — Los 30 puntos de precisión (2 semanas)

- [ ] **Tool `analizar_suscripciones`**: detección de recurrencia real en Python
      (comercio + importe + cadencia). Un qwen3:8b no va a escribir ese SQL solo, y una
      implementación determinista se defiende mucho mejor en la memoria.
      Debe responder: "¿a qué estoy suscrito?", "¿cuánto me cuestan al mes?", "¿alguna ha subido de precio?".
- [ ] **Few-shots de SQL** en el system prompt para los casos que falle la Fase 1
      (comparativas mes a mes, semanas, LIKE con nombres parciales).
- [ ] **Contexto**: `MAX_FILAS = 200` en `database.py` + el resultado íntegro al historial
      (`str(salida)` en `agent.py`) desbordan los 8192 tokens de contexto en un solo turno.
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

---

# ✅ Hecho

- [x] **Migración de Anthropic a proveedores OpenAI-compatibles**. Defaults `ollama` + `qwen3:8b`
      en `agent.py`/`config.py`, `.env` local creado.
- [x] **`llama3.1:8b` → `qwen3:8b`** (consulta simple 3,8 s; SQL → gráfico → conclusión ~22 s).
      El interruptor `/no_think` **no** funciona con la plantilla de qwen3 en Ollama; lo que
      funciona es `extra_body={"reasoning_effort": "none"}` por el endpoint OpenAI-compatible
      (implementado como `EXTRA_BODY`). Hay además un filtro `limpiar_razonamiento()` que quita
      restos de `<think>`.
- [x] **Historial de las rutas gestionadas por backend**: `responder_directo` registra en
      `self.historial` y `procesar` mete el mensaje de usuario antes de los atajos.
- [x] **Capa de seguridad**: PIN por evento WebSocket aparte, nunca entra en el historial del LLM.
- [x] **Tolerancia a modelos pequeños**: JSON corrupto → se devuelve el error al LLM para que se
      autocorrija; spec Vega-Lite como string → se parsea; iteración vacía → reintento.
- [x] Barra espaciadora para activar el micro + indicador de altavoz.
- [x] `arrancar_ollama.sh` con `OLLAMA_CONTEXT_LENGTH=8192`, `KEEP_ALIVE=30m`, `FLASH_ATTENTION=1`.

---

# Notas de contexto

- Buena parte de los bugs vienen de que un modelo local de 8B genera tool calls menos fiables que
  Claude: el código debe ser mucho más tolerante (parsear strings, reintentar, devolver errores al
  modelo).
- El stack que anuncia la cátedra es **Unicaja & Google Cloud** (+ NVIDIA). Ollama local es
  defendible para un prototipo, pero conviene tener preparada en la memoria la respuesta de cómo
  migraría a Vertex AI / Gemini, o por qué el modelo local es mejor decisión (coste, latencia,
  soberanía del dato financiero).
- El proyecto solapa con el **Reto 1** (voz realtime, LLM open source, casos de uso "Bizum por voz,
  saldos y seguridad"). El baremo oficial puntúa Conversación + Agilidad + Operaciones = 40 pts,
  así que esa parte cuenta y no hay que desmontarla.
