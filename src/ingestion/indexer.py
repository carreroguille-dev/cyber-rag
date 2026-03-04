from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client import models as qdrant_models

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="[indexer] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

PDF_PATH = Path(os.getenv("PDF_PATH", "data/guia_nacional_ciberincidentes.pdf"))
OCR_MODEL = os.getenv("OCR_MODEL", "gpt-5.2-2025-12-11")
OCR_CONCURRENCY = int(os.getenv("OCR_CONCURRENCY", "5"))

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
VOCAB_SIZE = 30_000

COLLECTION_CHUNKS = "guia_chunks"
COLLECTION_CACHE = "qa_cache"

BATCH_SIZE = 10


# ---------------------------------------------------------------------------
# Sparse vector 
# ---------------------------------------------------------------------------

def _texto_a_sparse(text: str) -> qdrant_models.SparseVector:
    import re
    tokens = re.findall(r"\b\w+\b", text.lower())
    tokens = [t for t in tokens if len(t) > 1]
    if not tokens:
        return qdrant_models.SparseVector(indices=[0], values=[0.0])

    counts = Counter(tokens)
    total = sum(counts.values())
    combined: dict[int, float] = {}
    for term, count in counts.items():
        idx = int(hashlib.md5(term.encode()).hexdigest(), 16) % VOCAB_SIZE
        combined[idx] = combined.get(idx, 0) + count / total

    return qdrant_models.SparseVector(
        indices=list(combined.keys()),
        values=list(combined.values()),
    )


# ---------------------------------------------------------------------------
# Qdrant — creación de colecciones
# ---------------------------------------------------------------------------

def _crear_colecciones(qdrant: QdrantClient) -> None:
    """Recrea ambas colecciones (borra si existen)."""
    for name in [COLLECTION_CHUNKS, COLLECTION_CACHE]:
        if qdrant.collection_exists(name):
            qdrant.delete_collection(name)
            logger.info("Colección '%s' borrada.", name)

    hnsw = qdrant_models.HnswConfigDiff(m=16, ef_construct=100)

    qdrant.create_collection(
        collection_name=COLLECTION_CHUNKS,
        vectors_config={
            "dense": qdrant_models.VectorParams(
                size=EMBEDDING_DIM,
                distance=qdrant_models.Distance.COSINE,
                hnsw_config=hnsw,
            )
        },
        sparse_vectors_config={
            "bm25": qdrant_models.SparseVectorParams(
                index=qdrant_models.SparseIndexParams(on_disk=False)
            )
        },
    )
    logger.info("Colección '%s' creada.", COLLECTION_CHUNKS)

    qdrant.create_collection(
        collection_name=COLLECTION_CACHE,
        vectors_config={
            "dense": qdrant_models.VectorParams(
                size=EMBEDDING_DIM,
                distance=qdrant_models.Distance.COSINE,
                hnsw_config=hnsw,
            )
        },
    )
    logger.info("Colección '%s' creada.", COLLECTION_CACHE)

    for field in ["seccion", "tipo_contenido", "chunk_id", "ambito"]:
        qdrant.create_payload_index(
            collection_name=COLLECTION_CHUNKS,
            field_name=field,
            field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
        )
    for field in ["pagina_inicio", "pagina_fin"]:
        qdrant.create_payload_index(
            collection_name=COLLECTION_CHUNKS,
            field_name=field,
            field_schema=qdrant_models.PayloadSchemaType.INTEGER,
        )
    logger.info("Índices de payload creados en '%s'.", COLLECTION_CHUNKS)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

async def _get_embedding(text: str, client: AsyncOpenAI) -> list[float]:
    response = await client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


# ---------------------------------------------------------------------------
# Indexación de chunks
# ---------------------------------------------------------------------------

async def _indexar_chunks(
    chunks: list,
    client: AsyncOpenAI,
    qdrant: QdrantClient,
) -> None:
    """Embebe e indexa los chunks en lotes para respetar rate limits."""
    total = len(chunks)
    for batch_start in range(0, total, BATCH_SIZE):
        batch = chunks[batch_start : batch_start + BATCH_SIZE]

        embeddings = await asyncio.gather(
            *[_get_embedding(c.texto, client) for c in batch]
        )

        points = []
        for i, (chunk, embedding) in enumerate(zip(batch, embeddings)):
            point_id = batch_start + i + 1
            sparse = _texto_a_sparse(chunk.texto)
            points.append(
                qdrant_models.PointStruct(
                    id=point_id,
                    vector={
                        "dense": embedding,
                        "bm25": sparse,
                    },
                    payload=chunk.to_payload(),
                )
            )

        qdrant.upsert(collection_name=COLLECTION_CHUNKS, points=points)
        logger.info(
            "Indexados chunks %d–%d / %d.",
            batch_start + 1,
            min(batch_start + BATCH_SIZE, total),
            total,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    if not PDF_PATH.exists():
        logger.error(
            "PDF no encontrado en '%s'. "
            "Coloca el PDF en ./data/ y configura PDF_PATH.",
            PDF_PATH,
        )
        sys.exit(1)

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key or not openai_key.startswith("sk-"):
        logger.error("OPENAI_API_KEY no configurada o inválida.")
        sys.exit(1)

    client = AsyncOpenAI(api_key=openai_key)
    qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    logger.info("Iniciando OCR del PDF: %s", PDF_PATH)
    from src.ingestion.ocr import pdf_to_markdown
    pages = await pdf_to_markdown(
        str(PDF_PATH), client, model=OCR_MODEL, concurrency=OCR_CONCURRENCY
    )
    logger.info("%d páginas procesadas.", len(pages))

    logger.info("Construyendo chunks desde markdown...")
    from src.ingestion.chunker import build_chunks
    chunks = build_chunks(pages)
    logger.info("%d chunks generados.", len(chunks))

    if not chunks:
        logger.error("No se generaron chunks. Revisa el OCR y el chunker.")
        sys.exit(1)

    _crear_colecciones(qdrant)

    logger.info("Indexando %d chunks en Qdrant...", len(chunks))
    await _indexar_chunks(chunks, client, qdrant)

    logger.info("Ingesta completa. Colección '%s' lista.", COLLECTION_CHUNKS)


if __name__ == "__main__":
    asyncio.run(main())
