**English** · [Español](README.es.md)

# Conversational banking assistant

**Unicaja & UGR AI Challenge**. A **voice and text** banking assistant that checks the balance, sends Bizum transfers behind a PIN, answers questions about the transaction history by turning **natural language into SQL**, detects recurring payments, **projects month-end spending**, and **decides and generates, in real time, the table or chart** that best reinforces each answer with no templates.

Runs **entirely locally** on `qwen3:8b` through Ollama. No API keys, no per-query cost.

---

## 1. Getting started (3 steps)

```bash
cd banco-conversacional     # everything else runs from here

# 1) Local model (no API key needed)
ollama pull qwen3:8b
./arrancar_ollama.sh        # in another terminal: starts Ollama with an 8192 context

# 2) Dependencies and synthetic data
pip install -r requirements.txt
cp .env.example .env        # already set up for Ollama, nothing to edit
python -m backend.seed      # creates banco.db with 24 months of transactions

# 3) Run
uvicorn backend.main:app --reload
```

Open **http://localhost:8000** (Chrome recommended: the browser's speech recognition works best there).

> ⚠️ **`arrancar_ollama.sh` is not optional.** It sets `OLLAMA_CONTEXT_LENGTH=8192`. With the default 4,096 the context overflows, Ollama truncates from the front, the system prompt is lost and quality degrades **with nothing to warn you**.

> ⚠️ **Regenerate `banco.db` before every demo.** The data is relative to *today*: as soon as the month rolls over, "this month" and "this week" come back empty.

> 🌐 **The interface needs internet, the assistant doesn't.** The model, the database and all the logic run locally; the browser, however, loads the charting library (Vega-Lite), the fonts and the merchant icons from the network. Offline, the assistant still answers and speaks, but **charts never get rendered** and the interface loses its fonts and icons.

**Demo path**, in order: *"¿Cuál es mi saldo?"* (balance) → *"¿Cuánto llevo gastado en gasolina este mes?"* (fuel spend this month — opens the SQL chip) → *"¿Y esta semana?"* (follow-up, uses context) → *"¿A qué estoy suscrito?"* (subscriptions — table) → *"¿Cuánto he gastado este mes comparado con el pasado?"* (month-over-month — chart) → *"¿Cuánto voy a gastar este mes?"* (projection) → *"Haz un bizum de 20 euros a María López"* (PIN + live balance update).

---

## 2. Architecture

```
┌───────────────────── BROWSER (frontend/index.html) ──────────────────────┐
│  🎙️ STT (Web Speech)   💬 Chat   📊 Vega-Lite   📋 Tables   🔊 TTS       │
└──────────────▲───────────────────────│───────────────────────────────────┘
               │ JSON events           │ { "mensaje": … } / { "pin": … }
               │                       ▼
┌──────────────┴──────────── BACKEND (FastAPI) ────────────────────────────┐
│  main.py ──▶ agent.py  «agentic loop»                                    │
│              │  qwen3:8b via Ollama + history + sentence streaming       │
│              │                                                           │
│              ├─▶ deterministic shortcuts: balance, Bizum (regex), PIN    │
│              │                                                           │
│              ▼ tool calling (tools.py) — 6 tools                         │
│   ┌───────────────┬──────────────┬───────────────┬────────────────────┐  │
│   │consultar_saldo│ enviar_bizum │ consultar_    │ analizar_          │  │
│   │listar_contac. │              │ movimientos   │ suscripciones      │  │
│   │               │              │ (text-to-SQL) │ proyectar_gasto    │  │
│   └───────┬───────┴──────┬───────┴───────┬───────┴─────────┬──────────┘  │
│           │banking_api.py│               │ database.py     │ analitica.py│
│           ▼              ▼               ▼ (read-only)     ▼             │
│                    SQLite banco.db  (seed.py, fixed seed)                │
│                                                                          │
│  graficos.py «visual engine» ──▶ after the answer, decides whether there │
│              is anything worth showing and asks the LLM for it, apart.   │
└──────────────────────────────────────────────────────────────────────────┘
```

**Core idea:** the reasoning lives in the LLM, but **whatever an 8B model does not do reliably is solved in code**. Every decision sits where it was *measured* to work best. That boundary is the design of the project, and `docs` documents it together with the measurements that justify it.

---

## 3. How each piece works

### 3.1 The agentic loop (`backend/agent.py`)

Every WebSocket connection creates an `Agente` with its own **history**, which is what makes follow-ups work: after *"how much did I spend on fuel this month?"*, a bare *"and this week?"* is understood on its own.

Per turn: the LLM is called in streaming mode with the system prompt, the history and the 6 tools; if it asks for tools they are executed, their results go back into the history and the loop repeats (8 iterations max); otherwise the answer is final.

- **Streaming by sentence, not by token.** The sentence is the unit the TTS can read without chopping words.
- **History trimmed at turn boundaries.**
- **Deterministic shortcuts** for balance and Bizum: they are resolved without going through the LLM and save ~3 s. The balance one is deliberately strict — it requires *every* word to be in an allow-list — because a false negative only costs latency while a false positive returns a wrong answer.

### 3.2 Text-to-SQL (`consultar_movimientos`)

The system prompt carries the database schema, today's date and five query examples. The LLM writes the SQL; `database.py` runs it with **defence in depth**:

1. SQLite connection in **read-only mode** (`mode=ro`): writing through this path is physically impossible.
2. A single statement, which must start with `SELECT` or `WITH`.
3. A keyword blacklist (`INSERT`, `DROP`, `PRAGMA`, `ATTACH`…).
4. A hard cap of **50 rows**. This is not only about latency: the result enters the history in full, and with an 8,192-token context a large result overflows it in a single turn.

**Self-correction:** if the query fails, the SQLite error is handed back to the LLM as the tool result and it fixes the query on the next pass. The interface shows a *"query failed (the AI is correcting itself)"* chip followed by the good one.

The generated SQL is **always shown** in a collapsible panel, for explainability.

### 3.3 Operations: Bizum with PIN

`banking_api.py` simulates the bank endpoints. They are the **only** functions with write access, always with bound parameters (`?`), never with SQL written by the LLM.

```
User:     "Haz un bizum de 20 euros a María"
Backend:  validates the AMOUNT (€0.50–€1,000) before anything else
          resolves the contact → "Did you mean María López?"
User:     "Sí"
Backend:  → opens the PIN keypad                     (pedir_pin event)
User:     types the PIN                              (auth_bizum event)
API:      checks the daily limit and balance, then debits — all in ONE transaction
UI:       the balance pill updates immediately
```

Four things that matter and are not visible:

- **The amount is validated before the PIN is requested.** Asking for a passcode to authorise an operation already known to fail is bad security design.
- **The PIN never enters the LLM history.** It travels over a separate event (`auth_bizum`), Python validates it, and the model never sees it.
- **The transfer is atomic.** Reading the balance, checking it and updating it all happen inside a `BEGIN IMMEDIATE` transaction. Without it, two concurrent transfers read the same balance, both pass the check and one overwrites the other: €1,200 leaves an account holding €1,000. This was reproduced and is covered by a test.
- **A €500 daily limit** for chat-initiated operations, on top of the per-operation limit.

### 3.4 Advanced analytics (`backend/analitica.py`)

**Recurring payments.** *"What am I subscribed to?"* is not a query, it is an inference: recurrence is not stored in any column. What gives it away is not the amount but the **regularity of the intervals** between charges. Validated against 500 different seeds: 0 false negatives, 1 false positive.

**Spend projection.** The naive rule of three — spend so far ÷ days elapsed × days in month — is useless, because rent and direct debits land early in the month. Mean error over 23 months of history:

| | day 5 | day 15 |
|---|---|---|
| rule of three | **174.5 %** | 46.1 % |
| fixed payments handled separately | 31.7 % | 13.6 % |
| **+ historical average** | **8.7 %** | **8.4 %** |

And **every projection comes with its own measured error bar**, computed by projecting each past month and measuring how far off it was: 0 % for rent (it is fixed), ~20 % for groceries, **105 % for outgoing Bizums**. A single blanket figure would have been convincing and wrong.

### 3.5 Visual engine: table or chart

| | who decides |
|---|---|
| is there anything to show? | **the backend**, a deterministic rule over the shape of the result |
| table or chart? | **the LLM** |
| which mark, axes, columns? and why? | **the LLM** |

**Why the "when" is not left to the model:** it was measured. Across four variants of the system prompt and three passes over the 44 evaluation questions, its decision proved unstable under *any* prompt edit, even edits that said nothing about charts. Five of the six questions that should have ended in a chart never produced one.

The spec is generated by the LLM in full and from scratch in a **dedicated call**, so *no templates* still holds: there is not a single spec in the repository. The data is injected by the backend, which already has it — that removes ~700 generated tokens per visual and eliminates invented figures at the root.

That call runs **after** `fin_respuesta`, which is what triggers the TTS: the user hears the answer immediately and the visual appears while they are still listening, preceded by a "preparing" indicator.

### 3.6 Voice

- **STT:** Web Speech API (`lang: es-ES`), with interim results shown as you speak.
- **TTS:** `speechSynthesis`, **sentence by sentence as they arrive**. On a long answer, playback starts **6 seconds earlier**.
- All in the browser: no network latency, no cost.

**Configurable voice.** The ear button in the header opens a panel with three settings — **voice** (from those the system offers in Spanish), **pitch** and **rate** — plus a *"Test settings"* button that speaks a sample sentence without touching the conversation. They are stored in `localStorage`.

### 3.7 Interface

- **Demo login.** A username/password screen shown first; the WebSocket is not opened until it is passed, and the name entered becomes the profile in the header. It is a mock-up of the customer experience, **not real authentication**: it is validated in the browser and does not protect the endpoint.
- **Personalisation.** Profile, theme colour and voice, all in `localStorage`. None of it reaches the backend or the model.
- **Bizum receipt.** Once a transfer is confirmed, the answer is accompanied by a receipt with recipient, date, description and amount, alongside the balance pill updating.

### 3.8 Synthetic data (`backend/seed.py`)

A deterministic generator (`random.seed(42)` → reproducible database) with ~1,000 transactions over **24 months**: salary, rent, utility bills, subscriptions, gym, annual car insurance, fuel, groceries, restaurants, Bizums… The 24 months are not arbitrary: with 15, the annual insurance had a single charge and could not possibly be detected as recurring.

### 3.9 WebSocket protocol

| Event (server → client) | Use in the UI |
|---|---|
| `inicio_respuesta` | creates the assistant bubble |
| `texto` | streaming, one sentence per event |
| `descartar_texto` | withdraws what was shown if the model ends up calling a tool |
| `tool_inicio` | spinner with status ("Querying history…") |
| `sql` | collapsible chip with the query |
| `visual_generando` | notice that a table or chart is being prepared |
| `grafico` / `tabla` | the card, including the *"why this representation"* line |
| `visual_cancelado` | withdraws the notice if nothing renderable came out |
| `pedir_pin` | opens the numeric keypad |
| `saldo` | balance pill in the header |
| `fin_respuesta` | closes the turn |
| `error` | notice in the chat |

Client → server: `{"mensaje": …}` and `{"type": "auth_bizum", "pin": …}`.

---

## 4. How it is measured (`tests/`)

Nothing above is claimed without measuring it. Three test suites:

```bash
python -m tests.test_bizum          # 35 checks on the money flow. No LLM, ~10 s
python -m tests.test_recurrencia    # detector against N seeds. No LLM, ~30 s
python -m tests.evaluar --repeticiones 3   # 44 questions. Needs Ollama, ~13 min
```

`evaluar.py` scores **four things**, because they fail for different reasons: tool choice, final answer, visual reinforcement when it is warranted (a table counts the same as a chart — the representation is the model's call), and whether the spec arrives renderable. It also measures **latency to speech** separately from total latency, because the visual engine runs afterwards on purpose.

Expected values are recomputed from the database on every run, so they do not go stale when it is regenerated.

**Current state** (44 questions × 3 passes, `qwen3:8b`):

| | |
|---|---|
| Tool choice | **97.6 %** |
| Final answer correct | **90.5 %** |
| Visual reinforcement when warranted | **27/27** |
| Specs that actually render | **21/21** |
| Latency to speech (median) | **3.3 s** |

---

## 5. Responsible AI

- **The LLM cannot write to the database.** Its only route is a read-only connection.
- **The PIN never reaches the model**: it travels over a separate event and Python validates it.
- **Every money operation requires a PIN**, with 3 attempts plus per-operation and daily limits.
- **The SQL is always shown**: anyone can see where each figure came from.
- **The projection states how much it trusts itself** instead of giving a bare number.
- **It never invents figures**: every amount comes from a tool.
- **Sessions expire**: 30 minutes of inactivity close the WebSocket. The login screen is a mock-up of the customer experience, not a security control.
- **100 % synthetic** data.

---

## 6. Layout

```
README.md · README.es.md · TODO.md
banco-conversacional/
├── requirements.txt · .env.example
├── arrancar_ollama.sh     ← ctx 8192; without it quality degrades silently
├── banco.db               ← generated by: python -m backend.seed
├── backend/
│   ├── config.py          ← model, limits, DB schema and system prompt
│   ├── seed.py            ← reproducible synthetic data (seed 42)
│   ├── database.py        ← read-only SQL + write transaction
│   ├── banking_api.py     ← mock APIs: balance, contacts and Bizum
│   ├── analitica.py       ← recurring payments and spend projection
│   ├── graficos.py        ← visual engine: gate + dedicated call
│   ├── tools.py           ← tool schemas + dispatcher
│   ├── agent.py           ← agentic loop, shortcuts and Bizum flow
│   └── main.py            ← FastAPI: WebSocket /ws + serves the frontend
├── frontend/
│   └── index.html         ← chat, voice, Vega-Lite and tables (no build, no npm)
├── docs/                  ← LaTeX report (main.tex + capitulos/)
└── tests/
    ├── preguntas.jsonl    ← 44 labelled questions
    ├── evaluar.py         ← accuracy bench
    ├── test_bizum.py      ← money flow, deterministic
    └── test_recurrencia.py← detector against N seeds
```

---

*Demonstration project with 100 % synthetic data. No operation touches real money.*
