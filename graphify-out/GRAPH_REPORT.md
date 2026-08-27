# Graph Report - .  (2026-08-27)

## Corpus Check
- Corpus is ~22,238 words - fits in a single context window. You may not need a graph.

## Summary
- 143 nodes · 230 edges · 18 communities (12 shown, 6 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 11 edges (avg confidence: 0.8)
- Token cost: 17,638 input · 1,084 output

## Community Hubs (Navigation)
- Analítica, API Bancaria y BD
- Agente Conversacional y Bizum
- Detección de Intención y Config
- Evaluación de Precisión
- Generación de Datos Ficticios
- Requisitos y Baremo del Reto
- Servidor FastAPI y WebSocket
- Proyecto y Dependencias
- Módulos del Backend
- Arranque de Ollama
- Voz en el Navegador
- Dependencia Ollama
- Cliente OpenAI para Ollama
- Charla del Reto
- Notas de Migración a Ollama
- TODO Reto 2

## God Nodes (most connected - your core abstractions)
1. `Agente` - 21 edges
2. `ejecutar_tool()` - 11 edges
3. `conexion_lectura()` - 9 edges
4. `api_consultar_saldo()` - 8 edges
5. `ejecutar_caso()` - 8 edges
6. `normalizar_texto()` - 7 edges
7. `detectar_pagos_recurrentes()` - 7 edges
8. `api_listar_contactos_bizum()` - 7 edges
9. `ejecutar_sql_seguro()` - 7 edges
10. `buscar_contacto_bizum()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `Habla con tu dinero (Frontend)` --references--> `Agente`  [INFERRED]
  frontend/index.html → banco-conversacional/backend/agent.py
- `Agente` --calls--> `Analítica Avanzada`  [EXTRACTED]
  banco-conversacional/backend/agent.py → backend/analitica.py
- `Habla con tu dinero (Frontend)` --calls--> `FastAPI App`  [EXTRACTED]
  frontend/index.html → backend/main.py
- `Agente` --references--> `Configuración y Prompts`  [EXTRACTED]
  banco-conversacional/backend/agent.py → backend/config.py
- `Agente` --calls--> `Tools Dispatcher`  [EXTRACTED]
  banco-conversacional/backend/agent.py → backend/tools.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Flujo de eventos WebSocket backend→frontend** — banco_conversacional_readme_protocolo_websocket, banco_conversacional_readme_bucle_agentico, banco_conversacional_frontend_index_conectar, banco_conversacional_frontend_index_manejarevento [EXTRACTED 1.00]
- **Mapeo de las piezas al baremo de 100 puntos del reto** — banco_conversacional_readme_baremo, banco_conversacional_readme_text_to_sql, banco_conversacional_readme_motor_visual, banco_conversacional_readme_bucle_agentico, banco_conversacional_readme_banking_api [EXTRACTED 1.00]
- **Bucle Agéntico de Procesamiento** — backend_agent_agente, backend_tools_tools, backend_config_config [EXTRACTED 1.00]
- **Arquitectura de Seguridad (Defensa en Profundidad)** — backend_database_db, backend_banking_api_api, frontend_index_html [EXTRACTED 0.90]
- **Flujo de Generative UI** — backend_agent_agente, backend_tools_tools, frontend_index_html [EXTRACTED 0.85]

## Communities (18 total, 6 thin omitted)

### Community 0 - "Analítica, API Bancaria y BD"
Cohesion: 0.11
Nodes (30): _analizar_comercio(), analizar_recurrencia(), _clasificar_cadencia(), detectar_pagos_recurrentes(), date, Analítica avanzada sobre el histórico de movimientos.  El reto nombra tres tipos, Devuelve (nombre, días, meses) de la cadencia si los intervalos son     regulare, Analiza los cargos de un comercio. None si no son recurrentes. (+22 more)

### Community 1 - "Agente Conversacional y Bizum"
Cohesion: 0.14
Nodes (17): Configuración y Prompts, FastAPI App, Agente, buscar_contacto_bizum(), formatear_euros(), limpiar_markdown_respuesta(), Elimina Markdown básico que algunos modelos locales añaden aunque el prompt diga, Valida el destinatario contra los contactos conocidos de Bizum.      Devuelve: (+9 more)

### Community 2 - "Detección de Intención y Config"
Cohesion: 0.15
Nodes (13): es_cancelacion_bizum(), es_confirmacion_bizum(), es_consulta_saldo(), extraer_peticion_bizum(), limpiar_razonamiento(), normalizar_texto(), Atajo: las preguntas por el saldo actual se resuelven con una llamada a la     A, Detecta peticiones de Bizum con distintos órdenes naturales.      Ejemplos admit (+5 more)

### Community 3 - "Evaluación de Precisión"
Cohesion: 0.31
Nodes (12): aparece_en(), ejecutar_caso(), es_numero(), formatos_es(), informe(), marca(), normalizar(), principal() (+4 more)

### Community 4 - "Generación de Datos Ficticios"
Cohesion: 0.27
Nodes (9): crear_bd(), generar_movimientos(), _meses(), date, Generación de la base de datos ficticia (requisito 2 del reto).  Genera ~24 mese, Itera el día 1 de cada mes del rango., main(), movimientos_de_semilla() (+1 more)

### Community 5 - "Requisitos y Baremo del Reto"
Cohesion: 0.18
Nodes (11): APIs bancarias ficticias (banking_api.py), Baremo del reto (100 pts), Bucle agéntico (backend/agent.py), Flujo Bizum con confirmación, ejecutar_sql_seguro (defensa en profundidad), IA Responsable (seguridad y explicabilidad), Motor visual dinámico (mostrar_grafico, Vega-Lite sin plantillas), Protocolo WebSocket (eventos servidor→cliente) (+3 more)

### Community 6 - "Servidor FastAPI y WebSocket"
Cohesion: 0.29
Nodes (4): Servidor FastAPI.  - GET  /          → sirve el frontend (frontend/index.html)., saldo(), websocket_chat(), WebSocket

### Community 7 - "Proyecto y Dependencias"
Cohesion: 0.33
Nodes (6): Habla con tu dinero — Asistente bancario conversacional, Reto IA de Unicaja & UGR (Cátedra IA Responsable en Finanzas), Dependencia anthropic>=0.40 (no importada, prescindible), Dependencia fastapi>=0.115, Dependencia python-dotenv>=1.0, Dependencia uvicorn[standard]>=0.30

### Community 8 - "Módulos del Backend"
Cohesion: 0.40
Nodes (5): Analítica Avanzada, Banking API, Database Layer, Generador de Datos Ficticios, Tools Dispatcher

### Community 9 - "Arranque de Ollama"
Cohesion: 0.40
Nodes (4): OLLAMA_CONTEXT_LENGTH, OLLAMA_FLASH_ATTENTION, OLLAMA_KEEP_ALIVE, arrancar_ollama.sh script

## Knowledge Gaps
- **19 isolated node(s):** `arrancar_ollama.sh script`, `OLLAMA_CONTEXT_LENGTH`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_FLASH_ATTENTION`, `Reto IA de Unicaja & UGR (Cátedra IA Responsable en Finanzas)` (+14 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Agente` connect `Agente Conversacional y Bizum` to `Módulos del Backend`, `Detección de Intención y Config`, `Evaluación de Precisión`, `Servidor FastAPI y WebSocket`?**
  _High betweenness centrality (0.219) - this node is a cross-community bridge._
- **Why does `ejecutar_caso()` connect `Evaluación de Precisión` to `Analítica, API Bancaria y BD`, `Agente Conversacional y Bizum`?**
  _High betweenness centrality (0.072) - this node is a cross-community bridge._
- **Why does `ejecutar_tool()` connect `Analítica, API Bancaria y BD` to `Agente Conversacional y Bizum`, `Detección de Intención y Config`?**
  _High betweenness centrality (0.070) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `Agente` (e.g. with `ejecutar_caso()` and `principal()`) actually correct?**
  _`Agente` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `ejecutar_caso()` (e.g. with `Agente` and `ejecutar_sql_seguro()`) actually correct?**
  _`ejecutar_caso()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `arrancar_ollama.sh script`, `OLLAMA_CONTEXT_LENGTH`, `OLLAMA_KEEP_ALIVE` to the rest of the system?**
  _19 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Analítica, API Bancaria y BD` be split into smaller, more focused modules?**
  _Cohesion score 0.1051693404634581 - nodes in this community are weakly interconnected._