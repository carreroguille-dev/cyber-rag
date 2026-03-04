# Cyber-RAG — Asistente IA sobre la Guía Nacional de Ciberincidentes

Sistema RAG agéntico sobre la **Guía Nacional de Notificación y Gestión de Ciberincidentes** (Consejo Nacional de Ciberseguridad, 2020). Permite consultar clasificación de incidentes, plazos de notificación, organismos responsables, procedimientos y términos del glosario, con respuestas fundamentadas exclusivamente en el documento.

---

## Descripción general

```
Pregunta del usuario → Guardrail → Caché semántica → Agente ReAct → Qdrant → Respuesta fundamentada
```

El sistema nunca responde desde conocimiento general. Cada afirmación está respaldada por un fragmento recuperado y citada con sección y número de página. Si la información no está en el documento, lo indica explícitamente.

---

## Arquitectura general

```mermaid
flowchart TD
    User([Usuario]) -->|pregunta| UI[Interfaz Gradio\npuerto 7860]

    UI --> GR[Guardrail\nfiltro 2 capas]
    GR -->|REJECT| UI
    GR -->|PASS| CACHE[Caché semántica\nQdrant qa_cache]

    CACHE -->|HIT — respuesta cacheada| UI
    CACHE -->|MISS| AGENT[Agente ReAct\ngpt-5.1]

    AGENT <-->|tool calls| TOOLS[5 Herramientas de recuperación]
    TOOLS <-->|búsqueda vectorial| QDRANT[(Qdrant\nguia_chunks)]

    AGENT -->|respuesta bruta| SYNTH[Sintetizador\npuntuación de confianza]
    SYNTH -->|respuesta limpia + metadatos| CACHE
    CACHE -->|almacenar| QDRANT
    SYNTH --> UI

    subgraph Ingesta["Pipeline de Ingesta (una sola vez)"]
        PDF[PDF\n55 páginas] --> RENDER[PyMuPDF\npágina → PNG]
        RENDER --> OCR[gpt-5.2-2025-12-11\nOCR por visión]
        OCR --> DISK[(Caché en disco\ndata/markdown_cache/)]
        DISK --> CHUNK[Chunker de Markdown\n212 chunks]
        CHUNK --> EMBED[text-embedding-3-small\n+ BM25 sparse]
        EMBED --> QDRANT
    end
```

---

## Pipeline de ingesta

El pipeline convierte el PDF en un índice vectorial consultable. Se ejecuta una sola vez; los arranques posteriores usan el volumen de Qdrant y la caché de markdown sin ninguna llamada a la API.

```mermaid
flowchart LR
    PDF[Fichero PDF] --> A

    subgraph A["1 · Renderizado (pdf_renderer.py)"]
        direction TB
        pymupdf[PyMuPDF\ndpi=150] --> pages["lista de\n(num_página, bytes PNG)"]
    end

    A --> B

    subgraph B["2 · OCR por visión (ocr.py)"]
        direction TB
        check{¿Caché hit?}
        check -->|sí| md_disk["Leer .md desde\ndata/markdown_cache/"]
        check -->|no| vision["gpt-5.2-2025-12-11\nAPI de visión\nmax_completion_tokens=2048"]
        vision --> save["Guardar en\npage_NNN.md"]
        save --> md_disk
    end

    B --> C

    subgraph C["3 · Chunking (chunker.py)"]
        direction TB
        assemble["Ensamblar markdown completo\ncon marcadores de página"] --> split
        split["Dividir en headings\nH1/H2"] --> detect

        detect{Tipo de sección}
        detect -->|"densidad de **Término**: > 25%"| glos["Un chunk\npor entrada de glosario\nglosario.term_id"]
        detect -->|"contiene |---|"| tables["Chunks narrativos +\nun chunk por tabla\ncaption normalizado a Tabla N"]
        detect -->|en otro caso| narr["Sub-división por H3\nluego ventanas de tokens\n400 tok / 50 solapamiento"]
    end

    C --> D

    subgraph D["4 · Indexación (indexer.py)"]
        direction TB
        emb["text-embedding-3-small\n1536 dims"] --> sparse["Vector sparse BM25\nhash trick MD5 % 30000"]
        sparse --> upsert["Qdrant upsert\nlotes de 10"]
    end
```

### Metadatos de cada chunk

Cada chunk almacenado en Qdrant incluye:

| Campo | Descripción |
|---|---|
| `chunk_id` | ID único, p. ej. `sec_6_1_0`, `glosario.ransomware` |
| `seccion` | Número estructural: `"6"`, `"6.1"`, `"A1"` |
| `subseccion` | Número de subsección cuando aplica |
| `titulo_seccion` | Encabezado legible por humanos |
| `pagina_inicio / pagina_fin` | Rango de páginas en el PDF |
| `tipo_contenido` | `narrative`, `table`, `glossary_term`, `procedure`, `criteria_list`, `legal_reference` |
| `tabla` | Caption normalizado `"Tabla N"` para chunks de tabla |
| `termino_glosario` | Término para chunks de glosario |
| `ambito` | `general`, `sector_publico`, `infraestructuras_criticas`, … |
| `terminos_clave` | Top-8 palabras clave por frecuencia |

---

## Sistema de Guardrail

Cada mensaje del usuario pasa por un filtro de dos capas antes de llegar al agente. El guardrail es permisivo ante preguntas ambiguas o fuera de tema — filtrar por relevancia es tarea del agente, no del guardrail.

```mermaid
flowchart TD
    Q([Pregunta del usuario]) --> L1

    subgraph L1["Capa 1 — Reglas deterministas (rules.py)  ·  ~0 ms"]
        len{¿2–600 palabras?}
        len -->|no| block1[BLOQUEAR\nINVALID_LENGTH]
        len -->|sí| inj{¿Coincide patrón\nde inyección?}
        inj -->|sí| block2[BLOQUEAR\nINJECTION_PATTERN]
        inj -->|no| pass1[PASS → Capa 2]
    end

    subgraph L2["Capa 2 — Clasificador LLM (classifier.py)  ·  ~300 ms"]
        nano["gpt-5-nano\nJSON: decision + razon\nmax_completion_tokens=80"]
        nano -->|REJECT| block3[BLOQUEAR]
        nano -->|PASS| pass2[PASS → Pipeline]
    end

    L1 --> L2
```

**Capa 1** comprueba 14 patrones regex (palabras clave de jailbreak, tokens de prompt-injection, intentos de extracción del system prompt) y valida el rango de longitud. Coste cero de LLM.

**Capa 2** envía la consulta a `gpt-5-nano` con un system prompt estricto que solo detecta intentos de manipulación, no contenido fuera de tema. Devuelve únicamente `PASS` o `REJECT`.

Ambas capas devuelven el mismo mensaje de rechazo opaco para no revelar qué capa bloqueó la consulta.

---

## Caché semántica

Antes de invocar al agente (costoso), el sistema comprueba si una pregunta semánticamente similar ya fue respondida.

```mermaid
flowchart LR
    Q[Consulta] --> emb[text-embedding-3-small]
    emb --> search["Qdrant query_points\ncolección qa_cache\nusing=dense"]
    search --> thresh{coseno ≥ 0.92?}
    thresh -->|HIT| return[Devolver respuesta cacheada\nincrementar frecuencia_hits]
    thresh -->|MISS| agent[Llamar al Agente]
    agent --> store["cache_store()\nQdrant upsert\nvector={'dense': embedding}"]
```

La colección de caché (`qa_cache`) usa los mismos embeddings `text-embedding-3-small` que el índice principal. Un umbral de **0.92** es suficientemente conservador para evitar falsos aciertos en preguntas semánticamente próximas pero distintas.

---

## Agente ReAct

El agente sigue un bucle **Razonar → Actuar → Observar** usando tool calling de OpenAI. Dispone de 5 herramientas de recuperación y un máximo de 8 llamadas a herramientas por consulta.

```mermaid
flowchart TD
    Q[Pregunta del usuario] --> sys[System prompt\n+ mensaje de usuario]
    sys --> llm["gpt-5.1\nmax_completion_tokens=2048\ntimeout=90s"]

    llm -->|finish_reason=stop| synth[Sintetizador]
    llm -->|tool_calls| batch["Ejecutar lote completo\nde tool calls\nuno a uno"]

    batch --> append["Añadir resultados de tools\nal historial de mensajes"]
    append --> check{tool_calls_count\n≥ 8?}
    check -->|no| llm
    check -->|yes| force["Forzar respuesta final\n'responde con lo que tienes'"]
    force --> llm2["gpt-5.1\nllamada final"] --> synth

    subgraph synth["Sintetizador (synthesizer.py)"]
        clean[Limpiar espacios] --> conf
        conf["Puntuación de confianza\n1.0 citas + chunks\n0.8 chunks sin citar\n0.5 sin chunks\n0.0 'no cubierto'"]
    end
```

### Herramientas de recuperación

| Herramienta | Cuándo usarla | Implementación |
|---|---|---|
| `hybrid_search` | Primera acción por defecto en la mayoría de consultas | Fusión RRF dense + BM25, filtros opcionales de sección/tipo |
| `get_table` | Preguntas que involucran una tabla numerada | Scroll por `tabla == "Tabla N"` |
| `get_section` | Vista completa de una sección | Scroll por campo `seccion` o `subseccion` |
| `get_context_window` | Contexto alrededor de un chunk específico | Búsqueda por proximidad de página `±window` páginas |
| `glossary_lookup` | Significado de un término técnico | Búsqueda exacta/parcial en campo `termino_glosario` |

### Búsqueda híbrida

```mermaid
flowchart LR
    Q[Texto de consulta] --> norm[Normalizar\nminúsculas, expandir abreviaturas]
    norm --> dense[text-embedding-3-small\nvector denso 1536 dims]
    norm --> sparse["Vector sparse BM25\nhash trick: MD5(término) % 30000\nfrecuencia de término normalizada"]

    dense --> prefetch1["Qdrant Prefetch\nusing=dense\nlimit=20"]
    sparse --> prefetch2["Qdrant Prefetch\nusing=bm25\nlimit=20"]

    prefetch1 --> rrf["Reciprocal Rank Fusion\nFusion.RRF"]
    prefetch2 --> rrf
    rrf --> top["Top-k chunks\ncon payload"]
```

---

## Colecciones de Qdrant

```mermaid
erDiagram
    GUIA_CHUNKS {
        string chunk_id PK
        string chunk_version
        string seccion
        string subseccion
        string titulo_seccion
        int pagina_inicio
        int pagina_fin
        string tipo_contenido
        string ambito
        string tabla
        bool es_tabla_dividida
        string termino_glosario
        string texto
        list terminos_clave
        vector dense_1536
        sparse bm25
    }

    QA_CACHE {
        uuid id PK
        string pregunta_original
        string respuesta
        list chunks_fuente
        string version_documento
        float confianza_respuesta
        int tool_calls_usados
        string timestamp_creacion
        string timestamp_ultimo_hit
        int frecuencia_hits
        vector dense_1536
    }
```

Ambas colecciones usan **vectores con nombre** (`vectors_config={"dense": VectorParams(...)}`). `guia_chunks` incorpora además un vector sparse `bm25` para la búsqueda híbrida.

---

## Estructura del proyecto

```
cyber-rag/
├── data/
│   ├── guia_nacional_notificacion_gestion_ciberincidentes.pdf
│   └── markdown_cache/          # Caché OCR — page_001.md … page_055.md
├── src/
│   ├── ingestion/
│   │   ├── pdf_renderer.py      # PDF → páginas PNG (PyMuPDF)
│   │   ├── ocr.py               # PNG → Markdown (gpt-5.2 visión + caché disco)
│   │   ├── chunker.py           # Markdown → objetos Chunk
│   │   └── indexer.py           # Orquestador: OCR → chunk → embed → upsert
│   ├── retrieval/
│   │   └── qdrant_client.py     # 5 tipos de consulta + búsqueda híbrida + vector sparse
│   ├── guardrail/
│   │   ├── __init__.py          # guardrail() unificado con logs de timing
│   │   ├── rules.py             # Capa 1: regex + longitud
│   │   └── classifier.py        # Capa 2: gpt-5-nano PASS/REJECT
│   ├── cache/
│   │   └── semantic_cache.py    # Lookup + almacenamiento + invalidación por TTL
│   ├── agent/
│   │   ├── agent.py             # Bucle ReAct (gpt-5.1, máx. 8 tool calls)
│   │   ├── tools.py             # Definiciones de tools + despachador execute_tool
│   │   └── synthesizer.py       # Limpieza de respuesta + puntuación de confianza
│   └── ui/
│       └── app.py               # Interfaz de chat Gradio
├── docs/                        # Documentos de diseño
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Tecnologías

| Componente | Tecnología |
|---|---|
| LLM — agente | `gpt-5.1` |
| LLM — OCR | `gpt-5.2-2025-12-11` (visión) |
| LLM — guardrail | `gpt-5-nano` |
| Embeddings | `text-embedding-3-small` (1536 dims) |
| Base de datos vectorial | Qdrant (vectores densos + sparse con nombre) |
| Renderizado de PDF | PyMuPDF |
| Interfaz de usuario | Gradio 6 |
| Entorno de ejecución | Python 3.11, Docker |

---

## Diario de desarrollo

El proceso de diseño y resolución está documentado en [`DEVLOG.md`](DEVLOG.md).

---

## Inicio rápido

**Requisitos previos:** Docker, una clave de API de OpenAI y el PDF en `data/`.

```bash
# 1. Configurar credenciales
cp .env.example .env
# Editar .env y establecer OPENAI_API_KEY=sk-...

# 2. Colocar el PDF
# data/guia_nacional_notificacion_gestion_ciberincidentes.pdf

# 3. Primera ejecución — ingesta el documento (OCR + embeddings, ~5 min la primera vez)
docker compose up --build

# 4. Arranques posteriores — usa la caché de markdown y el volumen de Qdrant (instantáneo)
docker compose up

# Interfaz disponible en http://localhost:7860
```

### Variables de entorno

| Variable | Por defecto | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | — | **Obligatoria** |
| `PDF_PATH` | `data/guia_nacional_notificacion_gestion_ciberincidentes.pdf` | Ruta al PDF fuente |
| `OCR_MODEL` | `gpt-5.2-2025-12-11` | Modelo de visión para OCR |
| `OCR_CONCURRENCY` | `5` | Peticiones OCR en paralelo |
| `MARKDOWN_CACHE_DIR` | `data/markdown_cache` | Directorio de caché OCR |
| `QDRANT_HOST` | `localhost` | Hostname de Qdrant |
| `QDRANT_PORT` | `6333` | Puerto de Qdrant |

### Re-ingesta

La caché OCR hace que en una re-ingesta solo se llame a la API de embeddings:

```bash
# Re-ingesta completa (re-embebe todos los chunks, reinicia la colección Qdrant)
docker compose run --rm ingest

# Forzar re-OCR de todas las páginas (borrar caché primero)
rm -rf data/markdown_cache/
docker compose run --rm ingest
```
