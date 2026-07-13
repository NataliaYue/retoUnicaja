# Graph Report - .  (2026-07-13)

## Corpus Check
- Corpus is ~13,002 words - fits in a single context window. You may not need a graph.

## Summary
- 110 nodes · 174 edges · 8 communities
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 14 edges (avg confidence: 0.92)
- Token cost: 63,987 input · 0 output

## Community Hubs (Navigation)
- Agente y flujo Bizum
- APIs bancarias y SQL seguro
- Documentación y plan TODO
- Frontend: chat, voz y gráficos
- Configuración y datos ficticios
- Dependencias y migración Ollama
- Servidor FastAPI y WebSocket

## God Nodes (most connected - your core abstractions)
1. `Agente` - 11 edges
2. `ejecutar_tool()` - 9 edges
3. `api_consultar_saldo()` - 8 edges
4. `manejarEvento()` - 8 edges
5. `normalizar_texto()` - 7 edges
6. `conexion_lectura()` - 7 edges
7. `Habla con tu dinero — Asistente bancario conversacional` - 7 edges
8. `buscar_contacto_bizum()` - 6 edges
9. `Migración de Anthropic a Ollama/OpenAI-compatible` - 6 edges
10. `formatear_euros()` - 5 edges

## Surprising Connections (you probably didn't know these)
- `Habla con tu dinero (README raíz)` --semantically_similar_to--> `Habla con tu dinero — Asistente bancario conversacional`  [INFERRED] [semantically similar]
  README.md → banco-conversacional/README.md
- `Nota: agent.py y requirements adaptados a Ollama` --semantically_similar_to--> `Migración de Anthropic a Ollama/OpenAI-compatible`  [INFERRED] [semantically similar]
  README.md → TODO.md
- `Set de evaluación de SQL (~30 preguntas, tests/preguntas.jsonl)` --semantically_similar_to--> `Set de evaluación tests/preguntas.jsonl (idea §6)`  [INFERRED] [semantically similar]
  TODO.md → banco-conversacional/README.md
- `TODO — Plan de trabajo del Reto IA` --references--> `Habla con tu dinero — Asistente bancario conversacional`  [EXTRACTED]
  TODO.md → banco-conversacional/README.md
- `Indicador de carga durante las tools (evento tool_inicio)` --conceptually_related_to--> `manejarEvento()`  [INFERRED]
  TODO.md → banco-conversacional/frontend/index.html

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Flujo de eventos WebSocket backend→frontend** — banco_conversacional_readme_protocolo_websocket, banco_conversacional_readme_bucle_agentico, banco_conversacional_frontend_index_conectar, banco_conversacional_frontend_index_manejarevento [EXTRACTED 1.00]
- **Interacción por voz manos libres (STT → enviar → respuesta → TTS)** — banco_conversacional_readme_voz_web_speech, banco_conversacional_frontend_index_reconocedor, banco_conversacional_frontend_index_enviar, banco_conversacional_frontend_index_hablar [EXTRACTED 1.00]
- **Mapeo de las piezas al baremo de 100 puntos del reto** — banco_conversacional_readme_baremo, banco_conversacional_readme_text_to_sql, banco_conversacional_readme_motor_visual, banco_conversacional_readme_bucle_agentico, banco_conversacional_readme_banking_api [EXTRACTED 1.00]

## Communities (8 total, 0 thin omitted)

### Community 0 - "Agente y flujo Bizum"
Cohesion: 0.15
Nodes (19): Agente, buscar_contacto_bizum(), es_cancelacion_bizum(), es_confirmacion_bizum(), es_consulta_saldo(), extraer_peticion_bizum(), formatear_euros(), limpiar_markdown_respuesta() (+11 more)

### Community 1 - "APIs bancarias y SQL seguro"
Cohesion: 0.15
Nodes (18): api_enviar_bizum(), api_listar_contactos_bizum(), APIs bancarias ficticias (requisito "Operaciones", 10 pts del baremo).  Simulan, GET /api/v1/bizum/contactos (ficticio).      Devuelve los contactos conocidos de, POST /api/v1/bizum/enviar (ficticio).      Validaciones de negocio (las mismas q, conexion_escritura(), conexion_lectura(), ejecutar_sql_seguro() (+10 more)

### Community 2 - "Documentación y plan TODO"
Cohesion: 0.14
Nodes (15): APIs bancarias ficticias (banking_api.py), Baremo del reto (100 pts), Bucle agéntico (backend/agent.py), Flujo Bizum con confirmación, ejecutar_sql_seguro (defensa en profundidad), IA Responsable (seguridad y explicabilidad), Datos ficticios deterministas (backend/seed.py), Set de evaluación tests/preguntas.jsonl (idea §6) (+7 more)

### Community 3 - "Frontend: chat, voz y gráficos"
Cohesion: 0.19
Nodes (10): bajar(), enviar(), hablar() — TTS con speechSynthesis, manejarEvento(), Reconocedor — SpeechRecognition (STT es-ES), Motor visual dinámico (mostrar_grafico, Vega-Lite sin plantillas), Protocolo WebSocket (eventos servidor→cliente), Voz en el navegador (Web Speech API: STT + TTS) (+2 more)

### Community 4 - "Configuración y datos ficticios"
Cohesion: 0.21
Nodes (9): Configuración central del proyecto.  Aquí vive todo lo que el resto de módulos n, System prompt del agente. Se genera en cada arranque para incluir la fecha actua, system_prompt(), crear_bd(), generar_movimientos(), _meses(), Generación de la base de datos ficticia (requisito 2 del reto).  Genera ~15 mese, Itera el día 1 de cada mes del rango. (+1 more)

### Community 5 - "Dependencias y migración Ollama"
Cohesion: 0.17
Nodes (12): Habla con tu dinero — Asistente bancario conversacional, Reto IA de Unicaja & UGR (Cátedra IA Responsable en Finanzas), Dependencia anthropic>=0.40 (no importada, prescindible), Dependencia fastapi>=0.115, Dependencia ollama (no importada, prescindible), Dependencia openai>=1.14.0 (cliente del endpoint OpenAI-compatible de Ollama), Dependencia python-dotenv>=1.0, Dependencia uvicorn[standard]>=0.30 (+4 more)

### Community 6 - "Servidor FastAPI y WebSocket"
Cohesion: 0.28
Nodes (6): api_consultar_saldo(), GET /api/v1/cuentas/saldo (ficticio)., Servidor FastAPI.  - GET  /          → sirve el frontend (frontend/index.html)., saldo(), websocket_chat(), WebSocket

## Knowledge Gaps
- **12 isolated node(s):** `Habla con tu dinero (README raíz)`, `Reto IA de Unicaja & UGR (Cátedra IA Responsable en Finanzas)`, `P0.2 Falso positivo en el atajo de saldo (es_consulta_saldo)`, `P0.4 El historial no registra respuestas gestionadas por el backend`, `Modelo local qwen3:8b (sustituye a llama3.1:8b)` (+7 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Baremo del reto (100 pts)` connect `Documentación y plan TODO` to `Frontend: chat, voz y gráficos`?**
  _High betweenness centrality (0.072) - this node is a cross-community bridge._
- **Why does `Motor visual dinámico (mostrar_grafico, Vega-Lite sin plantillas)` connect `Frontend: chat, voz y gráficos` to `Documentación y plan TODO`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `manejarEvento()` (e.g. with `Motor visual dinámico (mostrar_grafico, Vega-Lite sin plantillas)` and `Protocolo WebSocket (eventos servidor→cliente)`) actually correct?**
  _`manejarEvento()` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Habla con tu dinero (README raíz)`, `Reto IA de Unicaja & UGR (Cátedra IA Responsable en Finanzas)`, `P0.2 Falso positivo en el atajo de saldo (es_consulta_saldo)` to the rest of the system?**
  _12 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Documentación y plan TODO` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._