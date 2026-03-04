import os
import uuid
from datetime import datetime

from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client import models

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
DOCUMENT_VERSION = os.getenv("DOCUMENT_VERSION", "1.0")

qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CACHE_COLLECTION = "qa_cache"
EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_THRESHOLD = 0.92


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

async def get_embedding(text: str) -> list[float]:
    response = await openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

async def cache_lookup(query: str, threshold: float = DEFAULT_THRESHOLD) -> str | None:
    """
    Busca en la caché semántica.

    Args:
        query:     Pregunta del usuario (post-guardrail).
        threshold: Umbral de similitud cosine. Default: 0.92.

    Returns:
        Texto de la respuesta cacheada si HIT, None si MISS.
    """
    embedding = await get_embedding(query)

    results = qdrant.query_points(
        collection_name=CACHE_COLLECTION,
        query=embedding,
        using="dense",
        limit=1,
        score_threshold=threshold,
        with_payload=True,
    )

    if results.points:
        hit = results.points[0]
        qdrant.set_payload(
            collection_name=CACHE_COLLECTION,
            payload={
                "frecuencia_hits": hit.payload["frecuencia_hits"] + 1,
                "timestamp_ultimo_hit": datetime.utcnow().isoformat() + "Z",
            },
            points=[hit.id],
        )
        return hit.payload["respuesta"]

    return None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

async def cache_store(query: str, respuesta: str, metadata: dict) -> None:
    """
    Almacena una nueva respuesta en la caché.

    Args:
        query:     Pregunta original del usuario.
        respuesta: Texto de respuesta generado por el agente.
        metadata:  Objeto metadata devuelto por el agente:
                   {chunks_fuente, secciones, paginas, tool_calls,
                    confianza, tablas_consultadas}
    """
    embedding = await get_embedding(query)
    ahora = datetime.utcnow().isoformat() + "Z"

    qdrant.upsert(
        collection_name=CACHE_COLLECTION,
        points=[
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector={"dense": embedding},
                payload={
                    "pregunta_original": query,
                    "respuesta": respuesta,
                    "chunks_fuente": metadata.get("chunks_fuente", []),
                    "secciones_consultadas": metadata.get("secciones", []),
                    "paginas_referenciadas": metadata.get("paginas", []),
                    "timestamp_creacion": ahora,
                    "timestamp_ultimo_hit": ahora,
                    "frecuencia_hits": 0,
                    "version_documento": DOCUMENT_VERSION,
                    "confianza_respuesta": metadata.get("confianza", 1.0),
                    "tool_calls_usados": metadata.get("tool_calls", 0),
                },
            )
        ],
    )


# ---------------------------------------------------------------------------
# Invalidación (CACHE_DESIGN.md §6)
# ---------------------------------------------------------------------------

def invalidar_cache_por_version(nueva_version: str) -> None:
    """
    Elimina todas las entradas que NO sean de la versión indicada.
    Usar al actualizar el documento fuente.
    """
    qdrant.delete(
        collection_name=CACHE_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must_not=[
                    models.FieldCondition(
                        key="version_documento",
                        match=models.MatchValue(value=nueva_version),
                    )
                ]
            )
        ),
    )


def limpiar_cache_por_ttl(dias: int = 90) -> None:
    """
    Elimina entradas no accedidas en los últimos N días.
    """
    from datetime import timedelta

    fecha_limite = (datetime.utcnow() - timedelta(days=dias)).isoformat() + "Z"
    qdrant.delete(
        collection_name=CACHE_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="timestamp_ultimo_hit",
                        range=models.Range(lt=fecha_limite),
                    )
                ]
            )
        ),
    )


def invalidar_entrada(cache_id: str) -> None:
    """
    Elimina una entrada específica por su ID (UUID string).
    """
    qdrant.delete(
        collection_name=CACHE_COLLECTION,
        points_selector=models.PointIdsList(points=[cache_id]),
    )
