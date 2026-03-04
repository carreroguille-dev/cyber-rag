# Cyber-RAG — Asistente IA sobre la Guía Nacional de Ciberincidentes

Agentic RAG system over the **Guía Nacional de Notificación y Gestión de Ciberincidentes** (Consejo Nacional de Ciberseguridad, Spain, 2020). Ask questions about incident classification, notification deadlines, responsible authorities, procedures, and glossary terms — all grounded exclusively in the document.

---

## Overview

```
User question → Guardrail → Semantic Cache → ReAct Agent → Qdrant → Grounded answer
```

The system never answers from general knowledge. Every claim is backed by a retrieved chunk and cited with section and page number. If the information is not in the document, it says so explicitly.

---

## High-Level Architecture

```mermaid
flowchart TD
    User([User]) -->|query| UI[Gradio Chat UI\nport 7860]

    UI --> GR[Guardrail\n2-layer filter]
    GR -->|REJECT| UI
    GR -->|PASS| CACHE[Semantic Cache\nQdrant qa_cache]

    CACHE -->|HIT — cached answer| UI
    CACHE -->|MISS| AGENT[ReAct Agent\ngpt-5.1]

    AGENT <-->|tool calls| TOOLS[5 Retrieval Tools]
    TOOLS <-->|vector search| QDRANT[(Qdrant\nguia_chunks)]

    AGENT -->|raw answer| SYNTH[Synthesizer\nconfidence score]
    SYNTH -->|clean answer + metadata| CACHE
    CACHE -->|store| QDRANT
    SYNTH --> UI

    subgraph Ingestion["Ingestion Pipeline (one-time)"]
        PDF[PDF\n55 pages] --> RENDER[PyMuPDF\npage → PNG]
        RENDER --> OCR[gpt-5.2-2025-12-11\nVision OCR]
        OCR --> DISK[(Disk Cache\ndata/markdown_cache/)]
        DISK --> CHUNK[Markdown Chunker\n212 chunks]
        CHUNK --> EMBED[text-embedding-3-small\n+ sparse BM25]
        EMBED --> QDRANT
    end
```

---

## Ingestion Pipeline

The pipeline converts a PDF into a searchable vector index. It is run once; subsequent starts use the Qdrant volume and markdown cache — no API calls needed.

```mermaid
flowchart LR
    PDF[PDF file] --> A

    subgraph A["1 · Render (pdf_renderer.py)"]
        direction TB
        pymupdf[PyMuPDF\ndpi=150] --> pages["list of\n(page_num, PNG bytes)"]
    end

    A --> B

    subgraph B["2 · Vision OCR (ocr.py)"]
        direction TB
        check{Cache hit?}
        check -->|yes| md_disk["Read .md from\ndata/markdown_cache/"]
        check -->|no| vision["gpt-5.2-2025-12-11\nvision API\nmax_completion_tokens=2048"]
        vision --> save["Save to\npage_NNN.md"]
        save --> md_disk
    end

    B --> C

    subgraph C["3 · Chunking (chunker.py)"]
        direction TB
        assemble["Assemble full markdown\nwith page markers"] --> split
        split["Split on H1/H2\nheadings"] --> detect

        detect{Section type?}
        detect -->|"density of **Term**: > 25%"| glos["One chunk\nper glossary entry\nglosario.term_id"]
        detect -->|"contains |---|"| tables["Narrative chunks +\none chunk per table\ncaption normalized to Tabla N"]
        detect -->|otherwise| narr["H3 sub-split\nthen token windows\n400 tok / 50 overlap"]
    end

    C --> D

    subgraph D["4 · Indexing (indexer.py)"]
        direction TB
        emb["text-embedding-3-small\n1536 dims"] --> sparse["BM25 sparse vector\nhash trick MD5 % 30000"]
        sparse --> upsert["Qdrant upsert\nbatch=10"]
    end
```

### Chunk metadata

Every chunk stored in Qdrant carries:

| Field | Description |
|---|---|
| `chunk_id` | Unique ID, e.g. `sec_6_1_0`, `glosario.ransomware` |
| `seccion` | Structural number: `"6"`, `"6.1"`, `"A1"` |
| `subseccion` | Subsection number when applicable |
| `titulo_seccion` | Human-readable heading |
| `pagina_inicio / pagina_fin` | Page range in the PDF |
| `tipo_contenido` | `narrative`, `table`, `glossary_term`, `procedure`, `criteria_list`, `legal_reference` |
| `tabla` | Normalized caption `"Tabla N"` for table chunks |
| `termino_glosario` | Term string for glossary chunks |
| `ambito` | `general`, `sector_publico`, `infraestructuras_criticas`, … |
| `terminos_clave` | Top-8 keywords by frequency |

---

## Guardrail System

Every user message passes through a two-layer filter before reaching the agent. The guardrail is fail-open for ambiguous or off-topic questions — relevance filtering is the agent's job, not the guardrail's.

```mermaid
flowchart TD
    Q([User query]) --> L1

    subgraph L1["Layer 1 — Deterministic Rules (rules.py)  ·  ~0 ms"]
        len{2–600 words?}
        len -->|no| block1[BLOCK\nINVALID_LENGTH]
        len -->|yes| inj{Injection pattern\nregex match?}
        inj -->|yes| block2[BLOCK\nINJECTION_PATTERN]
        inj -->|no| pass1[PASS → Layer 2]
    end

    subgraph L2["Layer 2 — LLM Classifier (classifier.py)  ·  ~300 ms"]
        nano["gpt-5-nano\nJSON: decision + razon\nmax_completion_tokens=80"]
        nano -->|REJECT| block3[BLOCK]
        nano -->|PASS| pass2[PASS → Pipeline]
    end

    L1 --> L2
```

**Layer 1** checks 14 regex patterns (jailbreak keywords, prompt-injection tokens, system-prompt extraction attempts) and enforces a word-count range. Zero LLM cost.

**Layer 2** sends the query to `gpt-5-nano` with a strict system prompt that only detects manipulation attempts — not off-topic content. Returns only `PASS` or `REJECT`.

Both layers return the same opaque rejection message so as not to reveal which layer blocked the query.

---

## Semantic Cache

Before calling the (expensive) agent, the system checks whether a semantically similar question was already answered.

```mermaid
flowchart LR
    Q[Query] --> emb[text-embedding-3-small]
    emb --> search["Qdrant query_points\nqa_cache collection\nusing=dense"]
    search --> thresh{cosine ≥ 0.92?}
    thresh -->|HIT| return[Return cached answer\nincrement frecuencia_hits]
    thresh -->|MISS| agent[Call Agent]
    agent --> store["cache_store()\nqdrant upsert\nvector={'dense': embedding}"]
```

The cache collection (`qa_cache`) uses the same `text-embedding-3-small` embeddings as the main index. A threshold of **0.92** is conservative enough to avoid false hits on semantically close but distinct questions.

---

## ReAct Agent

The agent follows a **Reason → Act → Observe** loop using OpenAI tool calling. It has access to 5 retrieval tools and a maximum of 8 tool calls per query.

```mermaid
flowchart TD
    Q[User query] --> sys[System prompt\n+ user message]
    sys --> llm["gpt-5.1\nmax_completion_tokens=2048\ntimeout=90s"]

    llm -->|finish_reason=stop| synth[Synthesizer]
    llm -->|tool_calls| batch["Execute full\ntool call batch\none-by-one"]

    batch --> append["Append tool results\nto messages history"]
    append --> check{tool_calls_count\n≥ 8?}
    check -->|no| llm
    check -->|yes| force["Force final answer\n'respond with what you have'"]
    force --> llm2["gpt-5.1\nfinal call"] --> synth

    subgraph synth["Synthesizer (synthesizer.py)"]
        clean[Clean whitespace] --> conf
        conf["Confidence score\n1.0 cited + chunks\n0.8 chunks no cite\n0.5 no chunks\n0.0 'not covered'"]
    end
```

### Retrieval Tools

| Tool | When to use | Implementation |
|---|---|---|
| `hybrid_search` | Default first action for most queries | Dense + BM25 RRF fusion, optional section/type filters |
| `get_table` | Questions involving a numbered table | Scroll by `tabla == "Tabla N"` |
| `get_section` | Need full view of a section | Scroll by `seccion` or `subseccion` field |
| `get_context_window` | Need context around a specific chunk | Page-proximity search `±window` pages |
| `glossary_lookup` | Meaning of a technical term | Match/partial search on `termino_glosario` field |

### Hybrid Search

```mermaid
flowchart LR
    Q[Query text] --> norm[Normalize\nlowercase, expand abbrevs]
    norm --> dense[text-embedding-3-small\n1536-dim dense vector]
    norm --> sparse["BM25 sparse vector\nhash trick: MD5(term) % 30000\nnormalized term frequency"]

    dense --> prefetch1["Qdrant Prefetch\nusing=dense\nlimit=20"]
    sparse --> prefetch2["Qdrant Prefetch\nusing=bm25\nlimit=20"]

    prefetch1 --> rrf["Reciprocal Rank Fusion\nFusion.RRF"]
    prefetch2 --> rrf
    rrf --> top["Top-k chunks\nwith payload"]
```

---

## Qdrant Collections

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

Both collections use **named vectors** (`vectors_config={"dense": VectorParams(...)}`). `guia_chunks` additionally has a sparse `bm25` named vector for hybrid search.

---

## Project Structure

```
cyber-rag/
├── data/
│   ├── guia_nacional_notificacion_gestion_ciberincidentes.pdf
│   └── markdown_cache/          # OCR cache — page_001.md … page_055.md
├── src/
│   ├── ingestion/
│   │   ├── pdf_renderer.py      # PDF → PNG pages (PyMuPDF)
│   │   ├── ocr.py               # PNG → Markdown (gpt-5.2 vision + disk cache)
│   │   ├── chunker.py           # Markdown → Chunk objects
│   │   └── indexer.py           # Orchestrator: OCR → chunk → embed → upsert
│   ├── retrieval/
│   │   └── qdrant_client.py     # 5 query types + hybrid search + sparse vector
│   ├── guardrail/
│   │   ├── __init__.py          # Unified guardrail() with timing logs
│   │   ├── rules.py             # Layer 1: regex + length
│   │   └── classifier.py        # Layer 2: gpt-5-nano PASS/REJECT
│   ├── cache/
│   │   └── semantic_cache.py    # Lookup + store + TTL invalidation
│   ├── agent/
│   │   ├── agent.py             # ReAct loop (gpt-5.1, max 8 tool calls)
│   │   ├── tools.py             # Tool definitions + execute_tool dispatcher
│   │   └── synthesizer.py       # Response cleanup + confidence score
│   └── ui/
│       └── app.py               # Gradio chat interface
├── docs/                        # Design documents
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Stack

| Component | Technology |
|---|---|
| LLM — agent | `gpt-5.1` |
| LLM — OCR | `gpt-5.2-2025-12-11` (vision) |
| LLM — guardrail | `gpt-5-nano` |
| Embeddings | `text-embedding-3-small` (1536 dims) |
| Vector DB | Qdrant (dense + sparse named vectors) |
| PDF rendering | PyMuPDF |
| UI | Gradio 6 |
| Runtime | Python 3.11, Docker |

---

## Quick Start

**Prerequisites:** Docker, an OpenAI API key, and the PDF placed in `data/`.

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...

# 2. Place the PDF
# data/guia_nacional_notificacion_gestion_ciberincidentes.pdf

# 3. First run — ingests the document (OCR + embed, ~5 min first time)
docker compose up --build

# 4. Subsequent starts — uses cached markdown and Qdrant volume (instant)
docker compose up

# UI available at http://localhost:7860
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required** |
| `PDF_PATH` | `data/guia_nacional_notificacion_gestion_ciberincidentes.pdf` | Path to source PDF |
| `OCR_MODEL` | `gpt-5.2-2025-12-11` | Vision model for OCR |
| `OCR_CONCURRENCY` | `5` | Parallel OCR requests |
| `MARKDOWN_CACHE_DIR` | `data/markdown_cache` | OCR cache directory |
| `QDRANT_HOST` | `localhost` | Qdrant hostname |
| `QDRANT_PORT` | `6333` | Qdrant port |

### Re-ingesting

The OCR cache means only the embedding API is called on re-ingest:

```bash
# Full re-ingest (re-embeds all chunks, resets Qdrant collection)
docker compose run --rm ingest

# Force re-OCR of all pages (delete cache first)
rm -rf data/markdown_cache/
docker compose run --rm ingest
```
