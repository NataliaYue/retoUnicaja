README EL AGENT.PY Y CAMBIOS EN REQUIREMENTS PARA FUNCIONAR CON OLLAMA Y NO SOLO CON CLAUDE
# 🗣️ Habla con tu dinero — Asistente bancario conversacional

Proyecto base para el **Reto IA de Unicaja & UGR** (Cátedra IA Responsable en Finanzas). Asistente bancario por **voz y texto** que consulta saldo, envía Bizums, responde preguntas sobre el histórico de movimientos convirtiendo **lenguaje natural → SQL**, y **decide y genera gráficos en tiempo real** sin plantillas.

---

## 1. Puesta en marcha (3 pasos)

```bash
# 1) Modelo local (no hace falta ninguna clave de API)
ollama pull qwen3:8b
./arrancar_ollama.sh        # en otra terminal: arranca Ollama con contexto 8192

# 2) Dependencias y datos ficticios
pip install -r requirements.txt
cp .env.example .env        # ya viene configurado para Ollama, no hay que tocarlo
python -m backend.seed      # crea banco.db con 24 meses de movimientos

# 3) Arrancar
uvicorn backend.main:app --reload
```

Abre **http://localhost:8000** (Chrome recomendado: el reconocimiento de voz del navegador funciona mejor). Prueba: *"¿Cuánto llevo gastado en gasolina este mes?"* → *"¿Y esta semana?"* → *"¿Cuándo pagué el seguro del coche?"* → *"Compara mis gastos por categoría de los últimos 3 meses"* → *"Envía 20 € a María López por Bizum"*.

---

## 2. Arquitectura general

```
┌──────────────────────── NAVEGADOR (frontend/index.html) ────────────────────────┐
│  🎙️ STT (Web Speech API)   💬 Chat con streaming   📊 Vega-Lite   🔊 TTS        │
└───────────────▲──────────────────────│──────────────────────────────────────────┘
                │  eventos JSON        │  { "mensaje": "..." }
                │  (texto, sql,        ▼
                │  grafico, saldo)  WebSocket /ws
┌───────────────┴─────────────────── BACKEND (FastAPI) ───────────────────────────┐
│                                                                                  │
│   main.py ──▶ agent.py  «bucle agéntico»                                         │
│               │   LLM (Ollama / Qwen3) + historial de conversación + streaming   │
│               │                                                                  │
│               ▼ tool calling (tools.py)                                          │
│   ┌────────────────┬──────────────────┬─────────────────────┬────────────────┐  │
│   │ consultar_saldo│ enviar_bizum     │ consultar_movimientos│ mostrar_grafico│  │
│   │ (API ficticia) │ (API ficticia)   │ (text-to-SQL)        │ (Vega-Lite)    │  │
│   └───────┬────────┴────────┬─────────┴──────────┬──────────┴───────┬────────┘  │
│           │ banking_api.py  │                    │ database.py      │ → WS       │
│           ▼                 ▼                    ▼ (solo lectura)   ▼            │
│                        SQLite banco.db  (seed.py: datos ficticios)               │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Idea central:** el "motor de razonamiento" no es un chatbot con `if`s, es un **LLM con herramientas** (*tool calling*). El modelo decide en cada turno si responde directamente, si necesita llamar a una API bancaria, si debe escribir SQL, o si un gráfico ayuda — y con qué tipo de gráfico. Todo el "razonamiento" está en el modelo; el código solo le da capacidades y le pone límites de seguridad.

---

## 3. Cómo funciona cada pieza

### 3.1 El bucle agéntico (`backend/agent.py`)

Cada conexión WebSocket crea un `Agente` con su **historial de conversación** (por eso funcionan las preguntas de seguimiento: tras "¿cuánto gasté en gasolina este mes?", un "¿y esta semana?" se entiende porque el LLM ve todo el contexto).

Por cada mensaje del usuario:

1. Se llama al LLM **en streaming** con: system prompt + historial + definición de las 4 herramientas.
2. Cada token de texto se reenvía al navegador al instante (`{"type":"texto","delta":"..."}`).
3. Si el LLM termina con `stop_reason == "tool_use"`, se ejecutan las herramientas pedidas, sus resultados se añaden al historial como `tool_result` y **se vuelve al paso 1**. Así el modelo puede encadenar: SQL → ver resultados → decidir gráfico → redactar conclusión.
4. Si termina sin pedir herramientas, la respuesta es final y se emite `fin_respuesta` (el texto completo, que el frontend usa para el TTS).

Hay un tope de 8 iteraciones por mensaje para evitar bucles infinitos.

### 3.2 Text-to-SQL (`consultar_movimientos`) — los 30 puntos gordos

- El **system prompt** (`config.py`) incluye el esquema completo de la BD comentado, la fecha de hoy y recetas de fechas relativas en SQLite (`strftime`, `date('now', ...)`). El LLM escribe la consulta él mismo.
- El SQL se ejecuta en `database.py → ejecutar_sql_seguro()` con **defensa en profundidad**:
  1. Conexión SQLite abierta en **modo solo lectura** (`mode=ro`): físicamente imposible escribir por esta vía.
  2. Una única sentencia, que debe empezar por `SELECT` o `WITH`.
  3. Lista negra de palabras clave (`INSERT`, `DROP`, `PRAGMA`, `ATTACH`...).
  4. Máximo 200 filas devueltas (controla contexto y latencia).
- **Autocorrección:** si la consulta falla, el error de SQLite se devuelve al LLM como resultado de la herramienta; en la siguiente vuelta del bucle el modelo corrige su SQL y reintenta. En la interfaz se ve el chip "Consulta con error (la IA se autocorrige)" seguido de la corregida — transparencia que queda muy bien en la demo y en la memoria.
- El SQL generado siempre se muestra en la UI en un desplegable ⌕ (explicabilidad → "IA Responsable").

### 3.3 Operaciones (`consultar_saldo`, `enviar_bizum`)

`banking_api.py` simula los endpoints internos del banco (devuelven dicts con forma de respuesta de API). Son las **únicas** funciones con acceso de escritura a la BD, siempre con parámetros ligados (`?`), nunca con SQL del LLM.

Flujo de un Bizum (con confirmación, exigida en el system prompt):

```
Usuario:  "Mándale 20 euros a María"
LLM:      "Voy a enviar 20,00 € por Bizum a María López. ¿Lo confirmas?"   ← NO llama a la tool
Usuario:  "Sí"
LLM:      → tool enviar_bizum(destinatario="María López", cantidad=20)
API:      valida límites Bizum (0,50–1.000 €) y saldo → descuenta y registra el movimiento
UI:       la píldora de saldo de la cabecera se actualiza al momento (evento "saldo")
LLM:      "¡Hecho! He enviado 20 € a María López. Tu saldo es ahora de 1.944,51 €."
```

### 3.4 Motor visual dinámico (`mostrar_grafico`) — sin plantillas

En lugar de tener funciones tipo `pintar_barras()`, el LLM genera **la especificación Vega-Lite v5 completa, en JSON, desde cero**, con los datos reales incrustados (`data.values`) y títulos/ejes en español. El frontend solo hace `vegaEmbed(spec)`.

- **Lógica visual (10 pts):** el system prompt le pide razonar el tipo (serie temporal → línea/barras por periodo; distribución → barras ordenadas o donut; patrón cíclico → radial...) y la herramienta exige un campo `razonamiento`, que se muestra bajo el gráfico como *"Por qué este gráfico: …"*. La decisión es autónoma y **queda demostrada en pantalla**.
- **Sin plantillas (10 pts):** en el repositorio no existe ni una sola spec de gráfico; el backend valida únicamente que llegue un objeto con `data` y lo reenvía. Cambia la pregunta y cambia el gráfico.
- El frontend inyecta solo el **tema** (paleta y tipografía) para que cualquier gráfico case con la interfaz — estilo ≠ plantilla: la estructura la decide la IA.

### 3.5 Voz (entrada y salida)

- **STT:** Web Speech API del navegador (`SpeechRecognition`, `lang: es-ES`) con resultados intermedios visibles mientras hablas; al detectar el final, se envía solo.
- **TTS:** `speechSynthesis` con voz en español. Se activa con el botón 🔊 o automáticamente si tu último mensaje fue por voz (conversación manos libres).
- Ventaja: cero latencia añadida y cero coste (todo local en el navegador). El system prompt pide respuestas breves y sin Markdown precisamente para que suenen naturales leídas en voz alta.
- *Mejora opcional* (ver §6): sustituir por Whisper + un TTS neuronal, o por una Realtime API voz-a-voz.

### 3.6 Datos ficticios (`backend/seed.py`)

Generador determinista (semilla fija → BD reproducible para depurar y grabar el vídeo) con ~630 movimientos en ~15 meses: nómina, alquiler, recibos (luz/agua/internet), suscripciones, gimnasio, **seguro del coche anual** (la pregunta del enunciado), gasolina y supermercado semanales, restaurantes, Bizums, etc., con comercios españoles reales. El docstring incluye el prompt equivalente por si preferís generar los datos 100% con un LLM, como sugiere el reto.

### 3.7 Protocolo WebSocket

| Evento (servidor → cliente) | Contenido | Uso en la UI |
|---|---|---|
| `inicio_respuesta` | — | crea la burbuja del asistente |
| `texto` | `delta` | streaming token a token |
| `sql` | `sql`, `proposito`, `error` | chip desplegable con la consulta |
| `grafico` | `spec` (Vega-Lite), `razonamiento` | tarjeta de gráfico + explicación |
| `saldo` | `valor` | píldora de saldo de la cabecera |
| `fin_respuesta` | `texto` completo | dispara el TTS |
| `error` | `detalle` | aviso en el chat |

Cliente → servidor: `{"mensaje": "texto del usuario"}`.

---

## 4. Mapeo directo al baremo (100 pts)

| Criterio | Pts | Dónde se gana en este proyecto |
|---|---|---|
| Conversación | 15 | Historial completo por sesión (seguimientos, confirmaciones), system prompt con estilo natural en español |
| Agilidad | 15 | Streaming token a token por WebSocket + modelo local rápido (Ollama/Qwen3) + voz local sin latencia de red |
| Operaciones | 10 | `banking_api.py` como APIs ficticias invocadas vía *skills* (tools), con validaciones y confirmación de Bizum |
| Consultas NL→SQL | 30 | Esquema en el prompt, recetas de fechas, ejecución segura de solo lectura, autocorrección ante errores |
| Lógica visual | 10 | La IA decide el tipo de gráfico y su `razonamiento` se muestra en pantalla |
| Sin plantillas | 10 | Spec Vega-Lite completa generada por el LLM en cada respuesta; cero specs en el código |
| Vídeo + memoria | 10 | §5 de este README: guion sugerido y esqueleto de la memoria |

---

## 5. Entregables: guion del vídeo y esqueleto de la memoria

**Vídeo (máx. 3 min):** 0:00 interfaz + pregunta por voz "¿cuál es mi saldo?" → 0:25 "¿cuánto llevo gastado en gasolina este mes?" y abrir el chip SQL → 0:50 "¿y esta semana?" (demuestra contexto) → 1:10 "compara mis gastos por categoría de los últimos 3 meses" (gráfico + razonamiento) → 1:40 pregunta que fuerce OTRO tipo de gráfico, p. ej. "evolución de mis gastos mes a mes en el último año" (demuestra que no hay plantillas) → 2:10 Bizum con confirmación y saldo actualizándose → 2:40 diagrama de arquitectura en una diapositiva.

**Memoria:** (1) objetivo y alcance; (2) arquitectura (diagrama del §2); (3) diseño del agente y las tools; (4) text-to-SQL: prompt, seguridad y autocorrección, con la tabla de precisión del §7; (5) motor visual: por qué Vega-Lite generado por LLM; (6) IA responsable: solo lectura, confirmación de operaciones, explicabilidad del SQL, datos ficticios; (7) latencia medida; (8) líneas futuras.

---

## 6. Ideas para subir nota (orden coste/beneficio)

1. **Set de evaluación**: `tests/preguntas.jsonl` con 30 preguntas y su resultado esperado; script que las lanza contra el agente y mide precisión del SQL. Material de oro para la memoria.
2. **Streaming de TTS por frases**: trocear `texto` por puntuación y hablar cada frase según llega (baja aún más la latencia percibida).
3. **Whisper para STT** (`faster-whisper` en local o API): más robusto que el reconocedor del navegador.
4. **Realtime API voz-a-voz** (OpenAI Realtime / Gemini Live) con las mismas tools: demo espectacular, más complejidad.
5. Multiusuario ficticio (login simple + `cliente_id` en las consultas).

---

## 7. Estructura del repositorio

```
banco-conversacional/
├── README.md              ← este documento
├── requirements.txt
├── .env.example           ← proveedor (ollama) y modelo (qwen3:8b)
├── banco.db               ← se genera con: python -m backend.seed
├── backend/
│   ├── config.py          ← modelo, esquema BD, system prompt (¡el cerebro se ajusta aquí!)
│   ├── seed.py            ← generación de datos ficticios (reproducible, semilla 42)
│   ├── database.py        ← capa SQL segura de solo lectura para el LLM
│   ├── banking_api.py     ← APIs ficticias: saldo y Bizum (única vía de escritura)
│   ├── tools.py           ← esquemas de las 4 herramientas + dispatcher
│   ├── agent.py           ← bucle agéntico con streaming
│   └── main.py            ← FastAPI: WebSocket /ws + sirve el frontend
└── frontend/
    └── index.html         ← chat, voz (Web Speech API) y render Vega-Lite (sin build, sin npm)
```

## 8. 🛡️ Capa de Seguridad y Autenticación (Nuevas Features)

Para acercar el asistente a los estándares reales de la banca y garantizar un entorno seguro, se ha implementado una arquitectura de defensa en profundidad:

*   **Autenticación Inicial (Login):** La interfaz está bloqueada por defecto. La conexión WebSocket con el servidor (y por tanto, la instanciación del agente LLM) no se establece hasta que el usuario se identifica correctamente en el frontend.
*   **Cierre de sesión por inactividad (Timeout):** El backend monitoriza el flujo de mensajes. Si transcurren 5 minutos sin interacción, el servidor cierra automáticamente el WebSocket, destruyendo el historial y la sesión del agente.
*   **Step-up Authentication (Teclado Seguro):** Las operaciones críticas (como enviar un Bizum) no se pueden confirmar mediante texto libre en el chat. El backend envía una señal que despliega un teclado numérico virtual en pantalla para solicitar el PIN de operaciones.
*   **Privacidad Zero-Knowledge (El LLM no memoriza claves):** El PIN introducido viaja por el WebSocket bajo un tipo de evento distinto (`auth_bizum`). El código Python intercepta este evento y lo valida contra la base de datos de forma nativa. **La contraseña jamás se añade al historial de la conversación ni es leída por la IA**.
*   **Límites de riesgo (Control de fraude):** Se ha establecido un límite de seguridad estricto para las operaciones mediante el chatbot (500 € diarios). El backend suma en tiempo real los movimientos del día y bloquea la operación si se supera este umbral, previniendo el vaciado de cuentas en caso de sesión desatendida.
---

*Proyecto de demostración con datos 100% ficticios. Ninguna operación afecta a dinero real.*
