# PROJECT OVERVIEW
## RAG Agéntico — Guía Nacional de Notificación y Gestión de Ciberincidentes

---

## 1. Descripción del Proyecto

Sistema de consulta inteligente sobre la **Guía Nacional de Notificación y Gestión de Ciberincidentes** (aprobada por el Consejo Nacional de Ciberseguridad el 21 de febrero de 2020), implementado como un RAG Agéntico con caché semántica y capa de guardrail.

El sistema permite a usuarios (RSI, equipos SOC, personal de CSIRT, administradores de sistemas) consultar en lenguaje natural el contenido de la guía, obteniendo respuestas precisas con referencias a sección y página, capaces de razonar sobre múltiples fragmentos del documento de forma simultánea.

---

## 2. Características del Documento Fuente

Conocer el documento es esencial para diseñar correctamente el sistema.

| Característica | Detalle |
|---|---|
| Páginas | 55 |
| Secciones principales | 8 (Introducción, Objeto, Alcance, Ventanilla Única, Taxonomía, Notificación, Gestión, Métricas) |
| Anexos | 5 (PIC, Sector Público, Sector Privado, Marco Regulador, Glosario) |
| Tablas críticas | 13 (taxonomía, peligrosidad, impacto, notificación, plazos, métricas...) |
| Flujogramas | 6 ilustraciones con flujos de proceso |
| Terminología específica | CCN-CERT, INCIBE-CERT, CNPIC, ESP-DEF-CERT, CSIRT, ENS, NIS, PIC, APT, TLP... |
| Referencias cruzadas | Frecuentes entre secciones y tablas ("consultar Tabla 4", "según apartado 6.1") |
| Idioma | Español |

### Tipos de contenido identificados
- **Texto narrativo**: descripciones de organismos, procedimientos, contexto normativo
- **Tablas normativas**: clasificaciones con valores exactos que NO deben fragmentarse
- **Listas de criterios**: parámetros de peligrosidad e impacto
- **Flujogramas**: descritos textualmente en el documento, con ilustraciones
- **Glosario**: términos con definiciones atómicas (Anexo 5, ~40 términos)
- **Referencias legales**: normativa española y europea citada en el Anexo 4

---

## 3. Arquitectura General

```
┌─────────────────────────────────────────────────────────────────┐
│                          USUARIO                                │
└───────────────────────────────┬─────────────────────────────────┘
                                │ query
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    GUARDRAIL (gpt-5-nano)                     │
│                                                                 │
│  Capa 1 — Reglas deterministas (regex + keywords)              │
│  Capa 2 — Clasificador LLM ligero                              │
│                                                                 │
│  Salidas: VALID | ORIENTED | REJECTED                          │
└──────────────┬──────────────────┬──────────────────────────────┘
               │ VALID            │ ORIENTED / REJECTED
               ▼                  ▼
┌──────────────────────┐   Mensaje al usuario
│   CACHÉ SEMÁNTICA    │   (orientación o rechazo estándar)
│   (RAG Clásico)      │
│                      │
│  Colección: qa_cache │
│  Similitud cosine    │
│  Umbral: 0.92        │
└──────┬───────────────┘
       │ HIT                    │ MISS
       ▼                        ▼
 Respuesta                ┌─────────────────────────────────────┐
 cacheada                 │     AGENTE ORQUESTADOR (gpt-5.1)     │
 (instantánea)            │                                     │
                          │  Loop ReAct:                        │
                          │  Thought → Action → Observation     │
                          │                                     │
                          │  Tools disponibles:                 │
                          │  • hybrid_search()                  │
                          │  • keyword_search()                 │
                          │  • get_section()                    │
                          │  • get_context_window()             │
                          │  • get_table()                      │
                          └─────────────┬───────────────────────┘
                                        │
                                        ▼
                          ┌─────────────────────────────────────┐
                          │      KNOWLEDGE BASE (Qdrant)        │
                          │                                     │
                          │  Colección: guia_chunks             │
                          │  Índice denso  (embeddings)         │
                          │  Índice sparse (BM25)               │
                          │  Payload: metadatos por chunk       │
                          └─────────────────────────────────────┘
                                        │
                                        ▼
                          ┌─────────────────────────────────────┐
                          │      SÍNTESIS DE RESPUESTA          │
                          │  Con citas a sección y página       │
                          │  Guardado en caché qa_cache         │
                          └─────────────────────────────────────┘
```

---

## 4. Stack Tecnológico

| Componente | Tecnología | Justificación |
|---|---|---|
| Vector Store (PDF) | **Qdrant** | Búsqueda híbrida nativa (dense + sparse BM25), ligero, producción-ready |
| Caché semántica | **Qdrant** (colección separada) | Reutiliza infraestructura, sin dependencias extra |
| Guardrail | **gpt-5-nano** + reglas | Modelo ligero y económico, suficiente para clasificación ternaria |
| Agente RAG | **gpt-5.1** | Capacidad de razonamiento multi-paso, tool calling nativo |
| Embeddings | **text-embedding-3-small** (OpenAI) | Buena relación calidad/coste para español técnico |
| Parser PDF | **PyMuPDF (fitz)** | Mejor preservación de tablas y estructura para PDFs normativos |
| Orquestación | **Python** + Qdrant SDK + OpenAI SDK | Stack mínimo sin frameworks de alto nivel que oculten la lógica |

> **Nota sobre frameworks**: Se evita deliberadamente LangChain o LlamaIndex para mantener control total sobre el comportamiento del agente. Para un documento normativo donde la precisión es crítica, la transparencia del código supera la comodidad del framework.

---

## 5. Decisiones de Diseño y Justificación

### 5.1 Chunking por sección semántica, no por tokens
El documento tiene jerarquía clara y secciones cortas. Fragmentar por número fijo de tokens rompe tablas y referencias cruzadas. Cada chunk respeta los límites naturales de sección/subsección/tabla.

### 5.2 Búsqueda híbrida obligatoria
El dominio (ciberseguridad normativa española) combina texto narrativo con términos exactos (siglas, nombres de organismos, artículos legales). La búsqueda solo semántica "suaviza" términos técnicos y puede mezclar conceptos similares. BM25 ancla los términos exactos.

### 5.3 Umbral de caché conservador (0.92)
En un documento normativo, una respuesta ligeramente incorrecta puede tener consecuencias legales (plazos de notificación, organismos a los que reportar). Se prefiere un umbral alto que garantice precisión sobre un umbral bajo que maximice hit rate.

### 5.4 Mensaje de orientación para queries de ciberseguridad general
El guardrail distingue entre queries completamente ajenas al dominio (rechazo directo) y queries de ciberseguridad general no cubiertas por el PDF (orientación sin responder). Esto mejora la experiencia sin añadir riesgo de respuestas incorrectas.

### 5.5 Separación de modelos: gpt-5-nano para guardrail, gpt-5.1 para agente
El guardrail es una tarea de clasificación simple. Usar el mismo modelo potente que el agente RAG para esta tarea sería un desperdicio económico y de latencia. gpt-5-nano es suficiente para clasificación ternaria con alta precisión.

### 5.6 Sin MCP
No se integra MCP porque el sistema es autónomo sobre un único documento. MCP añadiría valor si el agente necesitara federar búsquedas entre múltiples sistemas (SIEM, bases de CVEs, etc.). Se reserva para futuras iteraciones.

---

## 6. Flujos de Usuario Principales

### Flujo 1: Consulta en caché (camino feliz rápido)
```
Query → Guardrail (VALID) → Caché HIT → Respuesta en <500ms
```

### Flujo 2: Consulta nueva simple
```
Query → Guardrail (VALID) → Caché MISS → Agente (1-2 tool calls) → 
Síntesis → Guardado en caché → Respuesta en ~3-5s
```

### Flujo 3: Consulta compleja multi-sección
```
Query → Guardrail (VALID) → Caché MISS → Agente (3-5 tool calls, 
razonamiento sobre tablas + texto) → Síntesis con múltiples citas → 
Guardado en caché → Respuesta en ~8-12s
```

### Flujo 4: Query de ciberseguridad general
```
Query → Guardrail (ORIENTED) → Mensaje de orientación → Fin
(sin acceso al RAG, sin coste de gpt-5.1)
```

### Flujo 5: Prompt injection
```
Query → Guardrail Capa 1 (Regex HIT) → Mensaje estándar → Fin
(sin llamada al LLM, coste cero)
```

---

## 7. Métricas de Éxito del Sistema

| Métrica | Objetivo | Cómo medirla |
|---|---|---|
| Precisión de respuestas | >90% respuestas correctas | Evaluación manual con test set |
| Tasa de alucinaciones | <5% | Verificación contra secciones citadas |
| Hit rate del caché | >40% en uso real | Logs de Qdrant |
| Latencia P50 (caché hit) | <500ms | Instrumentación |
| Latencia P50 (caché miss) | <10s | Instrumentación |
| Tasa de detección de injections | >99% | Test set de adversarial queries |
| Falsos positivos del guardrail | <2% | Queries legítimas rechazadas |

---

## 8. Estructura de Archivos del Proyecto

```
/
├── PROJECT_OVERVIEW.md          ← Este documento
├── CHUNKING_STRATEGY.md         ← Cómo fragmentar el PDF
├── KNOWLEDGE_BASE_SCHEMA.md     ← Esquema de Qdrant
├── AGENT_DESIGN.md              ← Diseño del agente y tools
├── CACHE_DESIGN.md              ← Diseño del caché semántico
├── GUARDRAIL_DESIGN.md          ← Diseño del guardrail
├── GRADIO_UI.md                 ← Interfaz de prototipo con Gradio
│
├── src/
│   ├── ingestion/
│   │   ├── parser.py            ← Extracción estructurada del PDF
│   │   ├── chunker.py           ← Lógica de chunking semántico
│   │   └── indexer.py           ← Carga en Qdrant
│   ├── guardrail/
│   │   ├── rules.py             ← Capa 1: reglas deterministas
│   │   └── classifier.py        ← Capa 2: clasificador gpt-5-nano
│   ├── retrieval/
│   │   └── qdrant_client.py     ← Wrapper de búsquedas en Qdrant
│   ├── agent/
│   │   ├── tools.py             ← Definición de tools del agente
│   │   ├── agent.py             ← Loop ReAct con gpt-5.1
│   │   └── synthesizer.py       ← Síntesis de respuesta con citas
│   ├── cache/
│   │   └── semantic_cache.py    ← Lógica de caché semántica
│   ├── ui/
│   │   └── app.py               ← Interfaz Gradio (chat)
│   └── main.py                  ← Punto de entrada del sistema
│
├── data/
│   └── guia_nacional_ciberincidentes.pdf
│
├── tests/
│   ├── test_guardrail.py        ← Test set de queries legítimas e injections
│   ├── test_retrieval.py        ← Validación de chunks recuperados
│   └── test_agent.py            ← Validación de respuestas end-to-end
│
├── docker-compose.yml           ← Orquestación completa (Qdrant + App + Ingesta)
├── Dockerfile                   ← Imagen de la aplicación Python
├── .env.example                 ← Variables de entorno necesarias
└── requirements.txt
```

---

## 8.1 Despliegue con Docker

El prototipo completo se levanta con un único comando:

```bash
docker compose up
```

Tres servicios coordinados:

| Servicio | Puerto | Descripción |
|---|---|---|
| `qdrant` | 6333 | Base de datos vectorial |
| `ingest` | — | Ejecuta la ingesta del PDF al arrancar (solo una vez) |
| `app` | 7860 | Aplicación Python + interfaz Gradio |

Una vez levantado, el chat está disponible en `http://localhost:7860`.

---

## 9. Limitaciones Conocidas

- **Fecha del documento**: La guía es de 2020. Referencias a normativa pueden haber evolucionado (NIS2, etc.). El sistema responde sobre el contenido del PDF tal como fue publicado.
- **Flujogramas**: Las ilustraciones del PDF son imágenes. El sistema trabaja con las descripciones textuales de los flujogramas, no con las imágenes en sí.
- **Idioma**: El sistema está optimizado para consultas en español. Consultas en otros idiomas pueden funcionar pero no están garantizadas.
- **Tablas complejas**: Algunas tablas con mucho contenido (Tabla 3, Tabla 5) pueden requerir múltiples tool calls del agente para cubrirse completamente.