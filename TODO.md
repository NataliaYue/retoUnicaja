# TODO — Reto IA Unicaja & UGR (Asistente bancario conversacional)

Baremo del reto (100 pts): Conversación 15 · Agilidad 15 · Operaciones 10 · Precisión consultas 30 · Lógica visual 10 · Gráficos sin plantillas 10 · Vídeo 5 · Memoria 5.
Fecha límite: **30 de septiembre**.
Ollama pull qwen3:8b
---

## 🔴 P0 — Bugs que rompen la demo (arreglar ya)

### 1. Los gráficos no funcionan (`mostrar_grafico`)
Hay varias causas probables encadenadas; revisar en este orden:

- [x] ~~**`MAX_TOKENS = 700` trunca la spec Vega-Lite**~~ **HECHO**: subido a 2000 en `backend/config.py`. Además se fijó `TEMPERATURA = 0.2` (con la temperatura por defecto qwen3 generaba SQL inválido de forma intermitente).
- [x] ~~**`json.loads(tc["arguments"])` sin proteger**~~ **HECHO**: ahora el JSON corrupto se devuelve al modelo como tool_result de error para que se autocorrija, igual que con el SQL.
- [x] **Historial envenenado con `content: None`** — **HECHO**: cuando el modelo devolvía una iteración vacía, se guardaba un mensaje de asistente con `content: None` y Ollama rechazaba TODA la conversación posterior con 400 `invalid message content type: <nil>`. Ahora nunca se guarda `None` (siempre string), las iteraciones vacías se reintentan una vez, y si persisten se emite un mensaje de fallback en lugar de dejar la respuesta en blanco.
- REVISAR PUES CAMBIO A QWEN QUIZA ARREGLÓ ESTO. **La spec puede llegar como string en vez de objeto** (`backend/tools.py:160`). Los modelos pequeños a menudo serializan el parámetro `spec` como string JSON. Si `isinstance(spec, str)`, intentar `json.loads(spec)` antes de rechazarla.
- [ ] **Validación demasiado estricta** (`backend/tools.py:165-187`): exige `title`, `mark` y `encoding` en el nivel raíz, pero specs válidas con `layer`, `hconcat` o `transform` no los llevan ahí. Relajar: exigir solo `data.values`; si falta lo demás, devolver el error al LLM en vez de descartar.
- [ ] **Acumulación de tool calls en streaming frágil** (`backend/agent.py:456-468`): con Ollama, `tool_call.id` y `function.name` pueden venir solo en el primer chunk o ser `None`. Si el primer chunk de un índice no trae nombre, se queda `None` para siempre. Guardar id/name en cuanto aparezcan (`if tool_call.id: ...`).
- [ ] **`MAX_ITERACIONES_AGENTE = 4`** (`backend/config.py:30`) puede quedarse corto para la cadena SQL → reintento → gráfico → conclusión. Si se agota el bucle, no se emite ningún texto (respuesta vacía). Subir a 6-8 y, al agotarse, emitir un mensaje de fallback.
- [ ] Probar de punta a punta con: *"Compara mis gastos por categoría de los últimos 3 meses"* y *"Evolución de mis gastos mes a mes en el último año"*.

### 2. Falso positivo en el atajo de saldo
- [ ] `es_consulta_saldo()` (`backend/agent.py:64`) usa `in` sobre substrings: *"¿cuánto tengo gastado en gasolina?"* contiene "cuanto tengo" → responde el saldo en vez de consultar movimientos. Excluir mensajes que contengan "gastado/gasté/gasto/pagado/pagué…" o usar regex con límites de palabra.

### 3. Código duplicado y muerto
- [ ] `backend/banking_api.py`: **todo el módulo está duplicado** (docstring, imports y las 3 funciones aparecen dos veces). Dejar una sola copia.
- [ ] `backend/tools.py:201-206`: código muerto tras el `return` de `mostrar_grafico`. Eliminar.

### 4. El historial no registra las respuestas gestionadas por el backend
- [ ] Los atajos de saldo, y todo el flujo de Bizum (confirmación, corrección de contacto, envío), responden sin añadir nada a `self.historial`. Después, el LLM no sabe que eso pasó: *"¿a quién acabo de enviar el bizum?"* falla. Añadir cada par usuario/asistente de las rutas directas al historial (afecta a los 15 pts de Conversación).

### 5. Configuración incoherente
- [x] ~~Sin `.env`, el proyecto arranca mal~~ **HECHO**: defaults cambiados a `ollama` + `qwen3:8b` en `agent.py`/`config.py`, y creado el `.env` local con esos valores.
- [ ] `requirements.txt`: los paquetes `anthropic` y `ollama` no se importan en ningún sitio y se pueden quitar. Ojo: el paquete `openai` SÍ es necesario aunque el proveedor sea Ollama — es la librería cliente con la que se habla con el endpoint OpenAI-compatible de Ollama (`http://localhost:11434/v1`).
- [ ] `README.md` (ambos): las instrucciones dicen "pon tu ANTHROPIC_API_KEY", pero el proyecto funciona con Ollama/OpenAI-compat. Actualizar la puesta en marcha (incluye `ollama pull llama3.1` y arrancar Ollama).

### 6. Configuración audio
- [] Arreglar performance de modelo bajo situaciones de comunicación natural voice to voice.

### 7. Conjunto test
- [] Hacer preguntas para test automatizadas (alrededor de 30 por ejemplo).
---

## 🟠 P1 — Puntos del baremo en juego

### Agilidad (15 pts): recuperar el streaming percibido
- [ ] `agent.py` ahora **bufferiza todo el texto** y solo lo emite al final de la iteración (para no mostrar texto previo a las tools). Esto mata la sensación de rapidez. Alternativa: empezar a emitir deltas en cuanto llegue texto y, si aparece un tool call en el mismo turno, cortar/limpiar; o emitir el buffer en cuanto termine el stream sin tool calls en vez de al final del bucle.
- [ ] Medir y anotar latencias reales (primer token, respuesta completa, con/sin gráfico) → tabla para la memoria.
- [ ] **Indicador de carga durante las tools**: cuando el agente llama a una herramienta que puede tardar (generar gráfico, SQL, Bizum), mostrar en el chat un circulito/spinner con estado (p. ej. "Generando gráfico…", "Consultando movimientos…"). Implementación: emitir desde el backend un evento nuevo `{"type": "tool_inicio", "nombre": ...}` justo antes de `ejecutar_tool` en `agent.py`, y en `index.html` pintar el spinner en el bloque actual y quitarlo al llegar el siguiente evento (`sql`, `grafico`, `texto`…). Hoy solo existe el punto parpadeante de la burbuja vacía, que no dice qué está pasando.

### Modelo local
- [x] **Cambiar `llama3.1:8b` → `qwen3:8b`** — **HECHO y verificado** (consulta simple 3,8 s; SQL → gráfico → conclusión ~22 s). Nota: el interruptor `/no_think` NO funciona con la plantilla de qwen3 en Ollama; lo que funciona es pasar `extra_body={"reasoning_effort": "none"}` por el endpoint OpenAI-compatible (implementado en `agent.py` como `EXTRA_BODY`). También hay un filtro `limpiar_razonamiento()` que quita restos de `<think>` del texto.
- [ ] Cuando exista el set de evaluación de SQL: comparar precisión y latencia de llama3.1:8b vs qwen3:8b vs qwen2.5:14b (este último no cabe en VRAM, ~9 GB en Q4 → más lento; solo si la precisión lo justifica). La tabla comparativa es material perfecto para la memoria.

### Precisión de consultas (30 pts, lo más valioso)
- [ ] Crear **set de evaluación** `tests/preguntas.jsonl` con ~30 preguntas y resultado esperado + script que las lanza contra el agente y mide el acierto del SQL. Es la mejor inversión: detecta regresiones y es material de oro para la memoria.
- [ ] Añadir 3-4 ejemplos few-shot de SQL al system prompt (casos que ahora fallen según el set de evaluación: semanas, comparativas de meses, LIKE con nombres).
- [ ] Probar preguntas con seguimiento ("¿y esta semana?", "¿y el mes pasado?") y ambiguas.

### Gráficos (20 pts)
- [ ] Tras arreglar P0.1: verificar que el `razonamiento` se muestra bajo cada gráfico (10 pts de lógica visual se demuestran en pantalla).
- [ ] Fallback robusto: si el modelo genera la spec sin datos o corrupta, reconstruir `data.values` desde el último resultado de `consultar_movimientos` en vez de fallar.
- [ ] Probar variedad: línea (evolución), barras (ranking), donut (distribución) — el jurado valorará que la estructura cambie con la pregunta.

### Operaciones (10 pts)
- [ ] Ampliar los patrones regex de `extraer_peticion_bizum` (p. ej. "bizum de 20 euros para María", "págale 15 € a Ana", cantidades escritas "veinte euros" — al menos las cifras con € pegado "20€").
- [ ] Probar el flujo completo por voz: petición → sugerencia de contacto → confirmación → saldo actualizado en cabecera.

### Conversación y voz (15 pts)
- [ ] TTS por frases: trocear el texto por puntuación y hablar cada frase según llega (baja la latencia percibida).
- [ ] Limitar el crecimiento del historial (resumir o recortar turnos antiguos) para que la latencia no se degrade en conversaciones largas.

---

## 🟡 P2 — Entregables (10 pts) y pulido

- [ ] **Vídeo demo ≤ 3 min** (guion sugerido en `banco-conversacional/README.md §5`): voz + saldo + SQL visible + seguimiento contextual + 2 gráficos de tipos distintos + Bizum con confirmación.
- [ ] **Memoria técnica**: arquitectura, diseño del agente y tools, text-to-SQL (seguridad + autocorrección + tabla de precisión del set de evaluación), motor visual, IA responsable, latencias medidas.
- [ ] Regenerar `banco.db` justo antes de grabar (`python -m backend.seed`): los datos se generan relativos a *hoy*, y con una BD vieja "este mes" sale vacío.
- [ ] Migrar `@app.on_event("startup")` (`backend/main.py:39`) a `lifespan` (deprecado en FastAPI).
- [ ] Manejo de errores visible en la UI: si Ollama no está arrancado, ahora sale un error críptico; mostrar un aviso claro.
- [ ] Revisar la UI en móvil / ventana estrecha.

---

## Notas de contexto

- El reto pide: interacción natural (voz+texto), operaciones (saldo + Bizum simulado), análisis del histórico en lenguaje natural, y **gráficos generados en tiempo real sin plantillas**.
- El proyecto se migró de Anthropic a proveedores OpenAI-compatibles (Ollama con `llama3.1` por defecto). Gran parte de los bugs de gráficos vienen de que un modelo local de 8B genera tool calls menos fiables que Claude: el código debe ser mucho más tolerante (parsear strings, reintentar, devolver errores al modelo).
