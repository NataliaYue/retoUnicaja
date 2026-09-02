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

---

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
- [ ] **Indicador durante las tools**: evento `{"type": "tool_inicio", "nombre": ...}` antes de
      `ejecutar_tool`, y spinner con estado en el frontend ("Consultando movimientos…"). Hoy solo
      hay el punto parpadeante de la burbuja vacía.
- [ ] **TTS por frases**: trocear por puntuación y hablar cada frase según llega.

### Operaciones (10 pts)
- [ ] Ampliar regex de `extraer_peticion_bizum`. Verificado que fallan hoy:
      "bizum de 20 euros para María", "págale 15 euros a Ana por bizum".
      Cuando falla no es fatal —cae al LLM, que tiene la tool— pero es más lento y menos fiable.
- [ ] Probar el flujo completo por voz: petición → sugerencia de contacto → PIN → saldo actualizado.

### Analítica predictiva
- [ ] **Proyección de gasto a fin de mes** / alerta de desviación vs. media histórica.
      "Analítica Predictiva" aparece literalmente en el enunciado y el sistema es 100 %
      retrospectivo. Encaja directa sobre el motor visual que ya existe.

---

# 🟡 Deuda técnica

- [ ] **El flujo Bizum sigue escrito dos veces**: `preparar_bizum_desde_backend()` y la rama del
      tool call en `_procesar()`. La validación del importe tapó la divergencia, pero no la causa,
      y `agent.py` va por ~1.000 líneas. Extraer un `iniciar_bizum()` común, o un `bizum.py`.
- [ ] **Fuga de conexiones SQLite**: `with conexion_lectura() as conn:` **no cierra** la conexión
      — el context manager de sqlite3 solo hace commit/rollback. Afecta a `database.py` y a todo
      `banking_api.py`. Cada consulta deja un descriptor abierto.
- [ ] `requirements.txt`: `anthropic` y `ollama` no se importan en ningún sitio, quitar.
      Ojo: `openai` SÍ es necesario aunque el proveedor sea Ollama — es el cliente del endpoint
      OpenAI-compatible (`http://localhost:11434/v1`).
- [ ] `README.md` (ambos): siguen diciendo "pon tu ANTHROPIC_API_KEY". Actualizar a Ollama
      (`ollama pull qwen3:8b`, `./arrancar_ollama.sh`). Es lo primero que ve quien clone el repo.
- [ ] Migrar `@app.on_event("startup")` (`main.py`) a `lifespan` (deprecado en FastAPI).
- [ ] Error visible en la UI si Ollama no está arrancado (hoy sale un error críptico).
- [ ] **Código muerto** en `index.html`: un bloque dentro de `if (Reconocedor)` lee `input.value`
      y hace `enviar()` en tiempo de carga de página, cuando el input siempre está vacío.
      Parece que debía ir dentro de `rec.onend`.
- [ ] **Móvil**: solo hay `<meta viewport>` y una regla de `prefers-reduced-motion`. Ningún
      `@media` de ancho, así que no sabemos cómo se ve en pantalla estrecha.

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
