# KNOWLEDGE BASE SCHEMA
## Colecciones Qdrant — RAG Ciberincidentes

---

## 1. Visión General

Se utilizan **dos colecciones Qdrant** en la misma instancia:

| Colección | Propósito | Nº vectores estimado |
|---|---|---|
| `guia_chunks` | Knowledge base del PDF indexado | ~212 |
| `qa_cache` | Caché semántica de preguntas respondidas | Crece con el uso |

Ambas colecciones comparten la misma instancia Qdrant pero son completamente independientes. El modelo de embeddings debe ser el mismo en ambas para que las distancias cosine sean comparables.

---

## 2. Colección `guia_chunks`

### 2.1 Configuración de la Colección

```python
from qdrant_client.models import (
    VectorParams, Distance,
    SparseVectorParams, SparseIndexParams,
    HnswConfigDiff, OptimizersConfigDiff
)

# Configuración de la colección
collection_config = {
    "collection_name": "guia_chunks",
    
    # Vector denso con nombre (embeddings semánticos)
    "vectors_config": {
        "dense": VectorParams(
            size=1536,
            distance=Distance.COSINE,
            on_disk=False
        )
    },

    # Vector disperso con nombre (BM25 léxico)
    "sparse_vectors_config": {
        "bm25": SparseVectorParams(
            index=SparseIndexParams(on_disk=False)
        )
    },
    
    # Optimizaciones para colección pequeña
    "hnsw_config": HnswConfigDiff(
        m=16,
        ef_construct=100,
        full_scan_threshold=10000  #
    ),
    
    "optimizers_config": OptimizersConfigDiff(
        indexing_threshold=0  
    )
}
```

### 2.2 Esquema del Payload

Cada punto en la colección tiene el siguiente payload:

```json
{
  // --- Identificación ---
  "chunk_id": "6.9",
  "chunk_version": "1.0",
  
  // --- Localización en el documento ---
  "seccion": "6",
  "subseccion": "6.4",
  "titulo_seccion": "Información a Notificar",
  "pagina_inicio": 25,
  "pagina_fin": 26,
  
  // --- Clasificación de contenido ---
  "tipo_contenido": "table",
  "ambito": "general",
  
  // --- Metadatos de tabla (solo si tipo_contenido = "table") ---
  "tabla": "Tabla 6",
  "es_tabla_dividida": false,
  "parte_tabla": null,
  "total_partes_tabla": null,
  
  // --- Metadatos de glosario (solo si tipo_contenido = "glossary_term") ---
  "termino_glosario": null,
  "categoria_glosario": null,
  
  // --- Contenido ---
  "texto": "...[contenido completo]...",
  "tokens_aproximados": 420,
  
  // --- Relaciones ---
  "referencias_cruzadas": ["6.1", "6.2", "6.3"],
  "terminos_clave": ["OSE", "PSD", "taxonomía", "peligrosidad", "impacto"]
}
```

### 2.3 Índices de Payload Recomendados

Para filtrado eficiente en las queries del agente:

```python
indices = [
    ("seccion", "keyword"),
    ("tipo_contenido", "keyword"),
    ("ambito", "keyword"),
    ("tabla", "keyword"),
    ("categoria_glosario", "keyword"),
    ("pagina_inicio", "integer"),
]
```

### 2.4 Tipos de Query Soportadas

#### Query A — Búsqueda híbrida (uso principal del agente)
Combina similitud semántica + BM25 léxico con fusión RRF (Reciprocal Rank Fusion):

```python
# hybrid_search(query, k=5, filtros=None)
results = client.query_points(
    collection_name="guia_chunks",
    prefetch=[
        # Búsqueda densa (semántica)
        models.Prefetch(
            query=embedding_vector,   
            using="dense",
            limit=20
        ),
        # Búsqueda dispersa (BM25 léxico)
        models.Prefetch(
            query=models.SparseVector(
                indices=bm25_indices,
                values=bm25_values
            ),
            using="bm25",
            limit=20
        )
    ],
    # Fusión RRF
    query=models.FusionQuery(fusion=models.Fusion.RRF),
    limit=k,
    query_filter=filtros  
)
```

#### Query B — Búsqueda por sección exacta
Para recuperar secciones completas por ID:

```python
# get_section(seccion_id)
results = client.scroll(
    collection_name="guia_chunks",
    scroll_filter=models.Filter(
        must=[
            models.FieldCondition(
                key="seccion",
                match=models.MatchValue(value="6")
            )
        ]
    ),
    limit=50,
    with_payload=True,
    with_vectors=False  
)
```

#### Query C — Recuperación de tabla completa
Para obtener todos los chunks de una tabla (incluyendo partes divididas):

```python
# get_table(nombre_tabla)
results = client.scroll(
    collection_name="guia_chunks",
    scroll_filter=models.Filter(
        must=[
            models.FieldCondition(
                key="tabla",
                match=models.MatchValue(value="Tabla 4")
            )
        ]
    ),
    limit=10,
    with_payload=True,
    with_vectors=False
)
# Ordenar por parte_tabla si es_tabla_dividida = True
```

#### Query D — Expansión de contexto
Para obtener chunks vecinos a un chunk dado en función de página:

```python
# get_context_window(chunk_id, window=2)
# Recupera chunks cuyo rango de páginas se solapa con ± window páginas
# del chunk de referencia. Funciona con cualquier formato de chunk_id.

def get_context_window(chunk_id: str, window: int = 2):
    # 1. Obtener la página del chunk de referencia
    ref = client.scroll(
        collection_name="guia_chunks",
        scroll_filter=models.Filter(
            must=[models.FieldCondition(
                key="chunk_id", match=models.MatchValue(value=chunk_id)
            )]
        ),
        limit=1, with_payload=True, with_vectors=False
    )
    if not ref[0]:
        return []
    ref_page = ref[0][0].payload["pagina_inicio"]

    # 2. Recuperar chunks en el rango de páginas adyacente
    results = client.scroll(
        collection_name="guia_chunks",
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="pagina_inicio",
                    range=models.Range(gte=ref_page - window, lte=ref_page + window)
                )
            ]
        ),
        limit=50,
        with_payload=True,
        with_vectors=False
    )
    return sorted(results[0], key=lambda x: x.payload["pagina_inicio"])
```

#### Query E — Búsqueda en glosario
Búsqueda específica en términos del glosario:

```python
# glossary_search(termino)
results = client.query_points(
    collection_name="guia_chunks",
    prefetch=[
        models.Prefetch(query=embedding, using="dense", limit=10)
    ],
    query=models.FusionQuery(fusion=models.Fusion.RRF),
    query_filter=models.Filter(
        must=[
            models.FieldCondition(
                key="tipo_contenido",
                match=models.MatchValue(value="glossary_term")
            )
        ]
    ),
    limit=3
)
```

---

## 3. Colección `qa_cache`

### 3.1 Configuración de la Colección

```python
cache_collection_config = {
    "collection_name": "qa_cache",

    # Vector con nombre (igual que guia_chunks para consistencia)
    "vectors_config": {
        "dense": VectorParams(
            size=1536,
            distance=Distance.COSINE,
            on_disk=False
        )
    },

    # El caché no necesita BM25, solo similitud semántica de preguntas
    "hnsw_config": HnswConfigDiff(
        m=16,
        ef_construct=100
    )
}
```

### 3.2 Esquema del Payload

```json
{
  // --- Identificación ---
  "cache_id": "uuid-v4",
  
  // --- Pregunta ---
  "pregunta_original": "¿Cuál es el plazo para notificar un incidente crítico?",
  "pregunta_normalizada": "plazo notificacion incidente critico",
  
  // --- Respuesta ---
  "respuesta": "Según la sección 6.5 (Tabla 7), los incidentes con nivel CRÍTICO requieren: notificación inicial inmediata, notificación intermedia en 24-48 horas, y notificación final en 20 días. Todos los plazos tienen como referencia el momento de remisión de la notificación inicial. (Fuente: Tabla 7, página 27)",
  
  // --- Trazabilidad ---
  "chunks_fuente": ["6.10", "6.6"],
  "secciones_consultadas": ["6.5", "6.1.3"],
  "paginas_referenciadas": [27, 23],
  
  // --- Control ---
  "timestamp_creacion": "2025-03-01T10:23:00Z",
  "timestamp_ultimo_hit": "2025-03-02T14:15:00Z",
  "frecuencia_hits": 14,
  "version_documento": "1.0",
  
  // --- Calidad ---
  "confianza_respuesta": 0.95,   # Score interno del agente
  "tool_calls_usados": 2
}
```

### 3.3 Índices del Caché

```python
cache_indices = [
    ("version_documento", "keyword"),  
    ("timestamp_creacion", "datetime"),
    ("frecuencia_hits", "integer"),
]
```

### 3.4 Query de Búsqueda en Caché

```python
# semantic_cache_lookup(query_embedding, threshold=0.92)
def cache_lookup(query_embedding, threshold: float = 0.92):
    results = client.query_points(
        collection_name="qa_cache",
        query=query_embedding,
        using="dense",   # colección usa vectores con nombre
        limit=1,
        score_threshold=threshold,
        with_payload=True
    )
    
    if results.points:
        hit = results.points[0]
        
        client.set_payload(
            collection_name="qa_cache",
            payload={
                "frecuencia_hits": hit.payload["frecuencia_hits"] + 1,
                "timestamp_ultimo_hit": datetime.utcnow().isoformat()
            },
            points=[hit.id]
        )
        return hit.payload["respuesta"]
    
    return None  # MISS
```

### 3.5 Guardado en Caché

```python
def cache_store(query: str, query_embedding, respuesta: str, metadata: dict):
    client.upsert(
        collection_name="qa_cache",
        points=[
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector={"dense": query_embedding},   # vector con nombre obligatorio
                payload={
                    "pregunta_original": query,
                    "pregunta_normalizada": normalizar(query),
                    "respuesta": respuesta,
                    "chunks_fuente": metadata["chunks_fuente"],
                    "secciones_consultadas": metadata["secciones"],
                    "paginas_referenciadas": metadata["paginas"],
                    "timestamp_creacion": datetime.utcnow().isoformat(),
                    "timestamp_ultimo_hit": datetime.utcnow().isoformat(),
                    "frecuencia_hits": 0,
                    "version_documento": "1.0",
                    "confianza_respuesta": metadata.get("confianza", 1.0),
                    "tool_calls_usados": metadata.get("tool_calls", 0)
                }
            )
        ]
    )
```

---

## 4. Gestión de la Instancia Qdrant

### 4.1 Docker Compose recomendado

```yaml
version: '3.8'
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"   
      - "6334:6334"  
    volumes:
      - qdrant_data:/qdrant/storage
    environment:
      QDRANT__SERVICE__GRPC_PORT: "6334"
      QDRANT__LOG_LEVEL: "INFO"
    restart: unless-stopped

volumes:
  qdrant_data:
```

### 4.2 Política de Backup

Para ~212 chunks del PDF (colección estática), el backup es trivial:
- La colección `guia_chunks` no cambia salvo actualización del documento. Snapshot manual tras la ingesta inicial.
- La colección `qa_cache` crece con el uso. Snapshot automático diario recomendado.

```python

client.create_snapshot(collection_name="guia_chunks")
client.create_snapshot(collection_name="qa_cache")
```

---

## 5. Modelo de Embeddings

### Selección

**Opción A — OpenAI text-embedding-3-small** (recomendada si hay acceso a API):
- Dimensiones: 1536
- Coste: $0.02 / 1M tokens
- Para 212 chunks × ~400 tokens promedio = ~85.000 tokens → ~$0.002 (ingesta única)
- Muy buena calidad en español técnico

**Opción B — nomic-embed-text** (open source, self-hosted):
- Dimensiones: 768 (ajustar `size` en la configuración de colección)
- Sin coste de API
- Requiere GPU o CPU dedicada para latencia aceptable

### Normalización de queries

Antes de generar el embedding de una query de usuario, aplicar normalización básica:

```python
def normalizar_query(query: str) -> str:
    query = query.lower().strip()
    query = re.sub(r'[¿¡]', '', query)
    abreviaciones = {
        "ccn": "ccn-cert",
        "incibe": "incibe-cert",
        "csirt": "computer security incident response team"
    }
    for abrev, expansion in abreviaciones.items():
        query = re.sub(rf'\b{abrev}\b', expansion, query)
    return query
```
