[English](README.md) · **Español**

# Asistente bancario conversacional

**Reto IA de Unicaja & UGR** (Cátedra IA Responsable en Finanzas). Asistente bancario por **voz y texto** que consulta el saldo, envía Bizums con PIN, responde sobre el histórico de movimientos convirtiendo **lenguaje natural → SQL**, detecta pagos recurrentes, **proyecta el gasto del mes** y **decide y genera en tiempo real la tabla o el gráfico** que mejor refuerza cada respuesta, sin plantillas.

Corre **entero en local** sobre `qwen3:8b` con Ollama. Sin claves de API y sin coste por consulta.

---

## 1. Puesta en marcha (3 pasos)

```bash
cd banco-conversacional     # todo lo demás se ejecuta desde aquí

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

Abre **http://localhost:8000** (Chrome recomendado: el reconocimiento de voz del navegador funciona mejor).

> ⚠️ **`arrancar_ollama.sh` no es opcional.** Fija `OLLAMA_CONTEXT_LENGTH=8192`; con los 4.096 por defecto el contexto desborda, Ollama trunca por delante, se pierde el system prompt y la calidad se cae **sin que nada lo avise**.

> ⚠️ **Regenera `banco.db` antes de cada demo.** Los datos son relativos a *hoy*: en cuanto cambia el mes, "este mes" y "esta semana" salen vacíos.

> 🌐 **La interfaz necesita internet, el asistente no.** El modelo, la base de datos y toda la lógica corren en local; el navegador, en cambio, carga de la red la librería de gráficos (Vega-Lite), las tipografías y los iconos de comercio. Sin conexión el asistente responde y habla igual, pero **los gráficos no llegan a pintarse** y la interfaz pierde fuentes e iconos.

**Recorrido de demo**, en orden: *"¿Cuál es mi saldo?"* → *"¿Cuánto llevo gastado en gasolina este mes?"* (abre el chip del SQL) → *"¿Y esta semana?"* (contexto) → *"¿A qué estoy suscrito?"* (tabla) → *"¿Cuánto he gastado este mes comparado con el pasado?"* (gráfico) → *"¿Cuánto voy a gastar este mes?"* (previsión) → *"Haz un bizum de 20 euros a María López"* (PIN + saldo actualizándose).

---

## 2. Arquitectura

```
┌──────────────────── NAVEGADOR (frontend/index.html) ─────────────────────┐
│  🎙️ STT (Web Speech)   💬 Chat   📊 Vega-Lite   📋 Tablas   🔊 TTS       │
└──────────────▲───────────────────────│───────────────────────────────────┘
               │ eventos JSON          │ { "mensaje": … } / { "pin": … }
               │                       ▼
┌──────────────┴──────────── BACKEND (FastAPI) ────────────────────────────┐
│  main.py ──▶ agent.py  «bucle agéntico»                                  │
│              │  qwen3:8b vía Ollama + historial + streaming por frases   │
│              │                                                           │
│              ├─▶ atajos deterministas: saldo, Bizum (regex), PIN         │
│              │                                                           │
│              ▼ tool calling (tools.py) — 6 herramientas                  │
│   ┌───────────────┬──────────────┬───────────────┬────────────────────┐  │
│   │consultar_saldo│ enviar_bizum │ consultar_    │ analizar_          │  │
│   │listar_contac. │              │ movimientos   │ suscripciones      │  │
│   │               │              │ (text-to-SQL) │ proyectar_gasto    │  │
│   └───────┬───────┴──────┬───────┴───────┬───────┴─────────┬──────────┘  │
│           │banking_api.py│               │ database.py     │ analitica.py│
│           ▼              ▼               ▼ (solo lectura)  ▼             │
│                    SQLite banco.db  (seed.py, semilla fija)              │
│                                                                          │
│  graficos.py «motor visual» ──▶ tras responder, decide si hay algo que   │
│              mostrar y le pide al LLM la tabla o el gráfico, aparte.     │
└──────────────────────────────────────────────────────────────────────────┘
```

**Idea central:** el razonamiento vive en el LLM, pero **lo que un modelo de 8B no hace de forma fiable se resuelve en código**. Cada decisión está donde se demostró —midiendo— que funciona mejor. Esa frontera es el diseño del proyecto y está documentada en `docs` con las mediciones que la justifican.

---

## 3. Cómo funciona cada pieza

### 3.1 El bucle agéntico (`backend/agent.py`)

Cada conexión WebSocket crea un `Agente` con su **historial**, que es lo que hace que funcionen los seguimientos: tras *"¿cuánto gasté en gasolina este mes?"*, un *"¿y esta semana?"* se entiende solo.

Por turno: se llama al LLM en streaming con el system prompt, el historial y las 6 herramientas; si pide herramientas se ejecutan, sus resultados vuelven al historial y se repite (máximo 8 iteraciones); si no, la respuesta es final.

- **Streaming por frases, no por tokens.** Es la unidad que el TTS lee sin cortar palabras.
- **Recorte del historial en frontera de turno.**
- **Atajos deterministas** para saldo y Bizum: se resuelven sin pasar por el LLM y ahorran ~3 s. El de saldo es deliberadamente estricto —exige que *todas* las palabras estén en una lista blanca—, porque un falso negativo solo cuesta latencia y un falso positivo da una respuesta incorrecta.

### 3.2 Text-to-SQL (`consultar_movimientos`)

El system prompt lleva el esquema de la BD, la fecha de hoy y cinco ejemplos de consulta. El LLM escribe el SQL; `database.py` lo ejecuta con **defensa en profundidad**:

1. Conexión SQLite en **modo solo lectura** (`mode=ro`): físicamente imposible escribir por esta vía.
2. Una única sentencia, que debe empezar por `SELECT` o `WITH`.
3. Lista negra de palabras clave (`INSERT`, `DROP`, `PRAGMA`, `ATTACH`…).
4. Máximo **50 filas**. No es solo latencia: el resultado entra entero en el historial, y con 8.192 tokens de contexto un resultado grande lo desborda en un solo turno.

**Autocorrección:** si la consulta falla, el error de SQLite se le devuelve al LLM como resultado de la herramienta y en la siguiente vuelta corrige. En la interfaz se ve el chip *"Consulta con error (la IA se autocorrige)"* seguido de la buena.

El SQL generado **siempre se muestra** en un desplegable para garantizar explicabilidad.

### 3.3 Operaciones: Bizum con PIN

`banking_api.py` simula los endpoints del banco. Son las **únicas** funciones con acceso de escritura, siempre con parámetros ligados (`?`), nunca con SQL del LLM.

```
Usuario:  "Haz un bizum de 20 euros a María"
Backend:  valida el IMPORTE (0,50–1.000 €) ANTES de nada
          resuelve el contacto → "¿Querías decir María López?"
Usuario:  "Sí"
Backend:  → despliega el teclado numérico del PIN   (evento pedir_pin)
Usuario:  teclea el PIN                             (evento auth_bizum)
API:      comprueba límite diario y saldo, y descuenta — todo en UNA transacción
UI:       la píldora de saldo se actualiza al momento
```

Cuatro cosas que importan y no se ven:

- **El importe se valida antes de pedir el PIN.** Pedir una clave para una operación que ya se sabe que va a fallar es mal diseño de seguridad.
- **El PIN nunca entra en el historial del LLM.** Viaja por un evento distinto (`auth_bizum`), lo valida Python, y la IA nunca lo ve.
- **El envío es atómico.** Leer el saldo, comprobarlo y actualizarlo va dentro de una transacción `BEGIN IMMEDIATE`. Sin eso, dos envíos simultáneos leen el mismo saldo, los dos pasan la comprobación y uno pisa al otro: salen 1.200 € de una cuenta con 1.000. Está reproducido y cubierto por un test.
- **Límite diario de 500 €** para las operaciones por chat, aparte del límite por operación.

### 3.4 Analítica avanzada (`backend/analitica.py`)

**Pagos recurrentes.** *"¿A qué estoy suscrito?"* no es una consulta, es una inferencia: la recurrencia no está en ninguna columna. Lo que la delata no es el importe sino la **regularidad de los intervalos** entre cargos. Validado contra 500 semillas distintas: 0 falsos negativos, 1 falso positivo.

**Proyección de gasto**. La regla de tres —gasto hasta hoy ÷ días × días del mes— es inservible, porque el alquiler y los recibos caen a principios de mes. Error medio sobre 23 meses del histórico:

| | día 5 | día 15 |
|---|---|---|
| regla de tres | **174,5 %** | 46,1 % |
| pagos fijos aparte | 31,7 % | 13,6 % |
| **+ media histórica** | **8,7 %** | **8,4 %** |

Y **cada proyección viene con su margen de error**, calculado proyectando cada mes pasado y midiendo cuánto falló: 0 % en alquiler (es fijo), ~20 % en supermercado, **105 % en bizums enviados**. Dar una cifra seca para todas habría sido creíble y falso.

### 3.5 Motor visual: tabla o gráfico

| | quién decide |
|---|---|
| ¿hay algo que mostrar? | **backend**, regla determinista sobre la forma del resultado |
| ¿tabla o gráfico? | **el LLM** |
| ¿qué marca, ejes, columnas? ¿por qué? | **el LLM** |

**Por qué el "cuándo" no lo decide el modelo:** está medido. Sobre cuatro variantes del prompt y tres vueltas de las 44 preguntas, su decisión resultó inestable ante *cualquier* edición del prompt, aunque no hablara de gráficos. Cinco de las seis preguntas que debían acabar en gráfico no pintaban nunca.

La spec la genera el LLM entera y desde cero en una **llamada dedicada**, así que *sin plantillas* se mantiene: en el repositorio no hay ni una sola spec. Los datos los inyecta el backend, que ya los tiene: quita ~700 tokens de generación por visual y elimina de raíz que se invente cifras.

Esa llamada va **después** de `fin_respuesta`, que es lo que dispara el TTS: el usuario oye la respuesta de inmediato y el visual aparece mientras la escucha, con un indicador de que se está preparando.

### 3.6 Voz

- **STT:** Web Speech API (`lang: es-ES`), con resultados intermedios visibles.
- **TTS:** `speechSynthesis`, **frase a frase según llegan**. En una respuesta larga se empieza a oír **6 segundos antes**.
- Todo local en el navegador: cero latencia de red y cero coste.

**Voz configurable.** El botón de la oreja en la cabecera abre un panel con tres ajustes —**voz** de las que el sistema ofrece en español, **tono** y **velocidad**— más un botón *"Probar ajustes"* que sintetiza una frase de muestra sin tocar la conversación. Se guardan en `localStorage`.

### 3.7 Interfaz

- **Login de demostración.** Pantalla previa de usuario y contraseña; el WebSocket no se abre hasta pasarla, y el nombre introducido se usa como perfil en la cabecera. Es una maqueta de cliente, **no autenticación real**: se valida en el navegador y no protege el endpoint.
- **Personalización.** Perfil, color del tema y voz, todo en `localStorage`. Ningún ajuste llega al backend ni al modelo.
- **Ticket de Bizum.** Al confirmarse un envío, la respuesta se acompaña de un justificante con destinatario, fecha, concepto e importe, además de la píldora de saldo actualizándose.

### 3.8 Datos ficticios (`backend/seed.py`)

Generador determinista (`random.seed(42)` → BD reproducible) con ~1.000 movimientos en **24 meses**: nómina, alquiler, recibos, suscripciones, gimnasio, seguro del coche anual, gasolina, supermercado, restaurantes, Bizums… Los 24 meses no son un capricho: con 15 el seguro anual solo tenía un cargo y era imposible detectarlo como recurrente.

### 3.9 Protocolo WebSocket

| Evento (servidor → cliente) | Uso en la UI |
|---|---|
| `inicio_respuesta` | crea la burbuja del asistente |
| `texto` | streaming, una frase por evento |
| `descartar_texto` | retira lo mostrado si el modelo acaba llamando a una tool |
| `tool_inicio` | spinner con estado ("Consultando histórico…") |
| `sql` | chip desplegable con la consulta |
| `visual_generando` | aviso de que se prepara una tabla o un gráfico |
| `grafico` / `tabla` | la tarjeta, con el *"por qué esta representación"* |
| `visual_cancelado` | retira el aviso si no sale nada pintable |
| `pedir_pin` | despliega el teclado numérico |
| `saldo` | píldora de saldo de la cabecera |
| `fin_respuesta` | cierra el turno |
| `error` | aviso en el chat |

Cliente → servidor: `{"mensaje": …}` y `{"type": "auth_bizum", "pin": …}`.

---

## 4. Cómo se mide (`tests/`)

Nada de lo de arriba se afirma sin medirlo. Tres batches de test:

```bash
python -m tests.test_bizum          # 35 comprobaciones del flujo de dinero. Sin LLM, ~10 s
python -m tests.test_recurrencia    # detector contra N semillas. Sin LLM, ~30 s
python -m tests.evaluar --repeticiones 3   # 44 preguntas. Necesita Ollama, ~13 min
```

`evaluar.py` puntúa **cuatro cosas**, porque fallan por motivos distintos: elección de herramienta, respuesta final, refuerzo visual cuando toca (cuenta igual tabla que gráfico: la representación la elige el modelo) y que la spec llegue pintable. Y mide **la latencia hasta la voz** aparte de la total, porque el motor visual va después a propósito.

Los valores esperados se recalculan desde la BD en cada ejecución, así que no caducan al regenerarla.

**Estado actual** (44 preguntas × 3 vueltas, `qwen3:8b`):

| | |
|---|---|
| Elección de herramienta | **97,6 %** |
| Respuesta final correcta | **90,5 %** |
| Refuerzo visual cuando toca | **27/27** |
| Specs que llegan a pintarse | **21/21** |
| Latencia hasta la voz (mediana) | **3,3 s** |

---

## 5. IA Responsable

- **El LLM no puede escribir en la base de datos.** Su única vía es una conexión en modo solo lectura.
- **El PIN nunca llega al modelo**: viaja por un evento aparte y lo valida Python.
- **Toda operación de dinero exige PIN**, con 3 intentos y límites de importe y diarios.
- **El SQL se muestra siempre**: cualquiera puede ver de dónde sale cada cifra.
- **La previsión dice lo que se fía de sí misma** en vez de dar una cifra seca.
- **Nunca inventa cifras**: toda cantidad sale de una herramienta.
- **Sesión con caducidad**: 30 min de inactividad cierran el WebSocket. El login previo es una maqueta de la experiencia de cliente, no una medida de seguridad.
- Datos **100 % ficticios**.

---

## 6. Estructura

```
README.md · README.es.md · TODO.md
banco-conversacional/
├── requirements.txt · .env.example
├── arrancar_ollama.sh     ← ctx 8192; sin esto la calidad cae en silencio
├── banco.db               ← se genera con: python -m backend.seed
├── backend/
│   ├── config.py          ← modelo, límites, esquema BD y system prompt
│   ├── seed.py            ← datos ficticios reproducibles (semilla 42)
│   ├── database.py        ← SQL de solo lectura + transacción de escritura
│   ├── banking_api.py     ← APIs ficticias: saldo, contactos y Bizum
│   ├── analitica.py       ← pagos recurrentes y proyección de gasto
│   ├── graficos.py        ← motor visual: gate + llamada dedicada
│   ├── tools.py           ← esquemas de las herramientas + dispatcher
│   ├── agent.py           ← bucle agéntico, atajos y flujo de Bizum
│   └── main.py            ← FastAPI: WebSocket /ws + sirve el frontend
├── frontend/
│   └── index.html         ← chat, voz, Vega-Lite y tablas (sin build, sin npm)
├── docs/                  ← memoria en LaTeX (main.tex + capitulos/)
└── tests/
    ├── preguntas.jsonl    ← 44 preguntas etiquetadas
    ├── evaluar.py         ← banco de precisión
    ├── test_bizum.py      ← flujo de dinero, determinista
    └── test_recurrencia.py← detector contra N semillas
```

---

*Proyecto de demostración con datos 100 % ficticios. Ninguna operación afecta a dinero real.*
