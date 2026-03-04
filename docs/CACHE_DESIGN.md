# CACHE DESIGN
## Caché Semántica de Preguntas y Respuestas

---

## 1. Propósito y Principio

La caché semántica intercepta preguntas que ya han sido respondidas anteriormente (o son suficientemente similares a una ya respondida) y devuelve la respuesta almacenada sin ejecutar el loop agéntico completo.

**¿Por qué RAG clásico en la caché?** El caché no necesita razonar. Solo necesita:
1. Convertir la nueva pregunta a embedding
2. Buscar similitud contra embeddings de preguntas previas
3. Si la similitud supera el umbral: devolver respuesta almacenada

Esto es precisamente la fortaleza del RAG clásico: embed → similarity search → retrieve. Sin agente, sin tool calls, sin coste de gpt-5.1.

**Ganancia**: Una query cacheada responde en ~200-400ms vs ~3-12s de una query nueva.

---

## 2. Flujo Completo con Caché

```
Query (post-guardrail VALID)
              │
              ▼
┌─────────────────────────────────┐
│  1. Generar embedding de query  │
│     (text-embedding-3-small)    │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│  2. Búsqueda en qa_cache        │
│     similarity_search(          │
│       embedding,                │
│       threshold=0.92            │
│     )                           │
└─────────────┬───────────────────┘
              │
    ┌─────────┴──────────┐
    │                    │
  HIT                  MISS
(score >= 0.92)    (score < 0.92)
    │                    │
    ▼                    ▼
┌──────────────┐   ┌────────────────────┐
│ Recuperar    │   │ Agente RAG         │
│ respuesta    │   │ (loop ReAct)       │
│ cacheada     │   └────────┬───────────┘
│              │            │
│ Actualizar:  │            ▼
│ - hits +1    │   ┌────────────────────┐
│ - timestamp  │   │ Síntesis de        │
└──────┬───────┘   │ respuesta          │
       │           └────────┬───────────┘
       │                    │
       │           ┌────────▼───────────┐
       │           │ Guardar en caché:  │
       │           │ - embedding query  │
       │           │ - respuesta        │
       │           │ - metadata         │
       │           └────────┬───────────┘
       │                    │
       └─────────┬──────────┘
                 │
                 ▼
          Respuesta al usuario
```

---

## 3. Umbral de Similitud

### Valor seleccionado: **0.92** (cosine similarity)

### Justificación para un documento normativo

En un documento de ciberseguridad con implicaciones legales (plazos de notificación, organismos receptores, niveles de obligatoriedad), responder con información de una pregunta "parecida pero diferente" puede tener consecuencias serias. Por eso se prioriza precisión sobre hit rate.

| Umbral | Hit rate estimado | Riesgo de respuesta incorrecta | Adecuado para este caso |
|---|---|---|---|
| 0.85 | Alto (~70%) | Alto | ❌ |
| 0.90 | Medio (~50%) | Bajo-medio | ⚠️ |
| **0.92** | **Medio-bajo (~35%)** | **Muy bajo** | **✅** |
| 0.95 | Bajo (~15%) | Mínimo | ⚠️ (demasiado conservador) |

### Ejemplos de similitudes esperadas

| Par de preguntas | Similitud esperada | Resultado esperado |
|---|---|---|
| "¿Plazo notificación incidente crítico?" vs "¿En cuánto tiempo notifico un incidente CRÍTICO?" | ~0.96 | HIT ✅ |
| "¿Qué es un APT?" vs "¿Cómo se define APT en la guía?" | ~0.95 | HIT ✅ |
| "¿Qué es un APT?" vs "¿Cómo se gestiona un APT?" | ~0.82 | MISS ✅ (correctamente) |
| "¿Plazo notificación CRÍTICO?" vs "¿Plazo notificación MUY ALTO?" | ~0.88 | MISS ✅ (son preguntas distintas) |
| "¿A quién reporto si soy AAPP?" vs "¿A quién reporto si soy empresa privada?" | ~0.85 | MISS ✅ (respuestas distintas) |

---

## 4. Esquema del Payload en qa_cache

```json
{
  "cache_id": "uuid-v4",

  "pregunta_original": "¿Cuál es el plazo para notificar un incidente crítico?",
  "pregunta_normalizada": "plazo notificacion incidente critico",

  "respuesta": "Según la sección 6.5 (Tabla 7), los incidentes con nivel CRÍTICO requieren: notificación inicial inmediata, notificación intermedia en 24-48 horas, y notificación final en 20 días. Todos los plazos tienen como referencia el momento de remisión de la notificación inicial. (Fuente: Tabla 7, página 27)",

  "chunks_fuente": ["6.10", "6.6"],
  "secciones_consultadas": ["6.5", "6.1.3"],
  "paginas_referenciadas": [27, 23],

  "timestamp_creacion": "2025-03-01T10:23:00Z",
  "timestamp_ultimo_hit": "2025-03-02T14:15:00Z",
  "frecuencia_hits": 14,
  "version_documento": "1.0",

  "confianza_respuesta": 0.95,
  "tool_calls_usados": 2
}
```

---

## 5. Implementación de la Búsqueda en Caché

```python
import uuid
from datetime import datetime
from openai import AsyncOpenAI
from qdrant_client import QdrantClient

openai_client = AsyncOpenAI()
qdrant_client = QdrantClient(host="localhost", port=6333)


async def get_embedding(text: str) -> list[float]:
    response = await openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding


async def cache_lookup(query: str, threshold: float = 0.92) -> str | None:
    """
    Busca en la caché semántica.
    Retorna la respuesta cacheada si hay HIT, None si MISS.
    """
    embedding = await get_embedding(query)

    results = qdrant_client.query_points(
        collection_name="qa_cache",
        query=embedding,
        using="dense",
        limit=1,
        score_threshold=threshold,
        with_payload=True
    )

    if results.points:
        hit = results.points[0]
        # Actualizar métricas del hit
        qdrant_client.set_payload(
            collection_name="qa_cache",
            payload={
                "frecuencia_hits": hit.payload["frecuencia_hits"] + 1,
                "timestamp_ultimo_hit": datetime.utcnow().isoformat()
            },
            points=[hit.id]
        )
        return hit.payload["respuesta"]

    return None  # MISS


async def cache_store(query: str, respuesta: str, metadata: dict) -> None:
    """
    Almacena una nueva respuesta en la caché.
    """
    embedding = await get_embedding(query)

    qdrant_client.upsert(
        collection_name="qa_cache",
        points=[{
            "id": str(uuid.uuid4()),
            "vector": {"dense": embedding},
            "payload": {
                "pregunta_original": query,
                "respuesta": respuesta,
                "chunks_fuente": metadata.get("chunks_fuente", []),
                "secciones_consultadas": metadata.get("secciones", []),
                "paginas_referenciadas": metadata.get("paginas", []),
                "timestamp_creacion": datetime.utcnow().isoformat(),
                "timestamp_ultimo_hit": datetime.utcnow().isoformat(),
                "frecuencia_hits": 0,
                "version_documento": "1.0",
                "confianza_respuesta": metadata.get("confianza", 1.0),
                "tool_calls_usados": metadata.get("tool_calls", 0)
            }
        }]
    )
```

---

## 6. Estrategia de Invalidación

### Caso 1 — Nueva versión del documento
Si se actualiza la guía, todo el caché queda obsoleto. Se purga por versión:

```python
def invalidar_cache_por_version(nueva_version: str):
    qdrant_client.delete(
        collection_name="qa_cache",
        points_selector={
            "filter": {
                "must_not": [{
                    "key": "version_documento",
                    "match": {"value": nueva_version}
                }]
            }
        }
    )
```

### Caso 2 — TTL por antigüedad (opcional)
Eliminar entradas no accedidas en los últimos N días:

```python
def limpiar_cache_por_ttl(dias: int = 90):
    from datetime import timedelta
    fecha_limite = (datetime.utcnow() - timedelta(days=dias)).isoformat()
    qdrant_client.delete(
        collection_name="qa_cache",
        points_selector={
            "filter": {
                "must": [{
                    "key": "timestamp_ultimo_hit",
                    "range": {"lt": fecha_limite}
                }]
            }
        }
    )
```

### Caso 3 — Invalidación manual de una entrada incorrecta
```python
def invalidar_entrada(cache_id: str):
    qdrant_client.delete(
        collection_name="qa_cache",
        points_selector={"points": [cache_id]}
    )
```

---

## 7. Preguntas Frecuentes Esperadas (Seed del Caché)

Pre-poblar el caché antes del primer uso en producción con las ~25 preguntas más frecuentes esperadas mejora la experiencia desde el primer día.

**Grupo 1 — Plazos de notificación**
- ¿Cuánto tiempo tengo para notificar un incidente CRÍTICO?
- ¿Cuánto tiempo tengo para notificar un incidente MUY ALTO?
- ¿Cuánto tiempo tengo para notificar un incidente ALTO?
- ¿Qué es la notificación inicial, intermedia y final?

**Grupo 2 — A quién reportar**
- ¿A quién reporto si soy una Administración Pública?
- ¿A quién reporto si soy una empresa privada?
- ¿A quién reporto si soy un operador de infraestructura crítica?
- ¿Qué es el CCN-CERT y cuándo lo contacto?
- ¿Qué es INCIBE-CERT y cuándo lo contacto?

**Grupo 3 — Clasificación de incidentes**
- ¿Cómo clasifico el nivel de peligrosidad de un incidente?
- ¿Cómo clasifico el nivel de impacto de un incidente?
- ¿Qué nivel tiene un ataque de ransomware?
- ¿Qué nivel tiene un DDoS?
- ¿Qué es un APT y qué nivel de peligrosidad tiene?

**Grupo 4 — Procedimiento de notificación**
- ¿Qué información debo incluir en la notificación de un incidente?
- ¿Qué es la ventanilla única de notificación?
- ¿Cómo funciona el sistema de ventanilla única paso a paso?

**Grupo 5 — Definiciones del glosario**
- ¿Qué es un CSIRT?
- ¿Qué es phishing según la guía?
- ¿Qué diferencia hay entre DoS y DDoS?

---

## 8. Métricas del Caché

| Métrica | Cómo calcularla | Objetivo |
|---|---|---|
| **Hit rate** | hits / (hits + misses) por período | >35% tras el primer mes |
| **Latencia en HIT** | tiempo desde query hasta respuesta | <500ms P95 |
| **Latencia en MISS** | tiempo desde query hasta respuesta | <12s P95 |
| **Tamaño del caché** | número de entradas en qa_cache | Monitorizar crecimiento |
| **Distribución de scores** | histograma de similitudes en búsquedas | Detectar si el umbral es óptimo |
| **Entradas más accedidas** | top 10 por frecuencia_hits | Identificar preguntas frecuentes |

### Revisión del umbral tras 30 días de uso real
- Hit rate < 20% → considerar bajar umbral a 0.90
- Hit rate > 60% con quejas de respuestas incorrectas → subir umbral a 0.94

---

## 9. Consideraciones de Concurrencia

- Las operaciones `upsert` en Qdrant son atómicas, no se requiere locking adicional.
- Si dos queries idénticas llegan simultáneamente y ambas son MISS, ambas ejecutarán el agente y ambas escribirán en caché (last-write-wins). El resultado es correcto en ambos casos.
- Para producción con >100 usuarios concurrentes, considerar una capa de deduplicación en memoria (Redis con TTL de 30s) para evitar el doble cómputo en queries simultáneas idénticas.