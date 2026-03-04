import hashlib
import os
import re
from collections import Counter

from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client import models

# ---------------------------------------------------------------------------
# Configuración de clientes
# ---------------------------------------------------------------------------

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

COLLECTION_NAME = "guia_chunks"
EMBEDDING_MODEL = "text-embedding-3-small"
VOCAB_SIZE = 30_000


# ---------------------------------------------------------------------------
# Normalización de queries 
# ---------------------------------------------------------------------------

_ABREVIACIONES = {
    "ccn": "ccn-cert",
    "incibe": "incibe-cert",
    "csirt": "computer security incident response team",
}


def normalizar_query(query: str) -> str:
    """
    Preprocesamiento básico antes de generar el embedding:
    - Minúsculas y strip
    - Elimina signos de interrogación/exclamación españoles
    - Expande abreviaciones conocidas del dominio
    """
    query = query.lower().strip()
    query = re.sub(r"[¿¡]", "", query)
    for abrev, expansion in _ABREVIACIONES.items():
        query = re.sub(rf"\b{abrev}\b", expansion, query)
    return query


# ---------------------------------------------------------------------------
# Sparse vector 
# ---------------------------------------------------------------------------

def texto_a_sparse_vector(text: str) -> models.SparseVector:
    """
    Convierte texto a SparseVector mediante hash trick:
    - Tokeniza por palabras, elimina tokens de 1 carácter
    - Calcula frecuencia normalizada por término
    - Mapea cada término a un índice entero en [0, VOCAB_SIZE)
    - Colapsa colisiones de hash sumando los pesos

    Mismo hash en ingesta y en query → comparación coherente.
    """
    tokens = re.findall(r"\b\w+\b", text.lower())
    tokens = [t for t in tokens if len(t) > 1]
    if not tokens:
        return models.SparseVector(indices=[0], values=[0.0])

    counts = Counter(tokens)
    total = sum(counts.values())

    combined: dict[int, float] = {}
    for term, count in counts.items():
        idx = int(hashlib.md5(term.encode()).hexdigest(), 16) % VOCAB_SIZE
        combined[idx] = combined.get(idx, 0) + count / total

    return models.SparseVector(
        indices=list(combined.keys()),
        values=list(combined.values()),
    )


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

async def get_embedding(text: str) -> list[float]:
    response = await openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


# ---------------------------------------------------------------------------
# Query A — Búsqueda híbrida (uso principal del agente)
# ---------------------------------------------------------------------------

async def hybrid_search(
    query: str,
    k: int = 5,
    filtro_seccion: str | None = None,
    filtro_tipo: str | None = None,
) -> list[dict]:
    """
    Búsqueda híbrida semántica + BM25 con fusión RRF.

    Args:
        query:          Términos de búsqueda en español.
        k:              Número de chunks a devolver. Default: 5.
        filtro_seccion: Filtrar por sección (ej: "6", "A1"). Opcional.
        filtro_tipo:    Filtrar por tipo de contenido. Opcional.

    Returns:
        Lista de payloads de los chunks más relevantes.
    """
    normalized = normalizar_query(query)
    embedding = await get_embedding(normalized)
    sparse = texto_a_sparse_vector(normalized)

    conditions = []
    if filtro_seccion:
        conditions.append(
            models.FieldCondition(
                key="seccion",
                match=models.MatchValue(value=filtro_seccion),
            )
        )
    if filtro_tipo:
        conditions.append(
            models.FieldCondition(
                key="tipo_contenido",
                match=models.MatchValue(value=filtro_tipo),
            )
        )
    query_filter = models.Filter(must=conditions) if conditions else None

    results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        prefetch=[
            models.Prefetch(query=embedding, using="dense", limit=20),
            models.Prefetch(query=sparse, using="bm25", limit=20),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=k,
        query_filter=query_filter,
        with_payload=True,
    )
    return [p.payload for p in results.points]


# ---------------------------------------------------------------------------
# Query B — Recuperar sección completa
# ---------------------------------------------------------------------------

def get_section(seccion_id: str) -> list[dict]:
    """
    Devuelve todos los chunks de una sección o subsección.

    Args:
        seccion_id: "6" para sección 6 completa, "6.1" para subsección.
                    Secciones de anexo: "A1", "A2", ..., "A5".

    Returns:
        Lista de payloads ordenados por chunk_id.
    """
    if "." in seccion_id:
        key, value = "subseccion", seccion_id
    else:
        key, value = "seccion", seccion_id

    results, _ = qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key=key,
                    match=models.MatchValue(value=value),
                )
            ]
        ),
        limit=50,
        with_payload=True,
        with_vectors=False,
    )
    chunks = [p.payload for p in results]
    chunks.sort(key=lambda c: c.get("chunk_id", ""))
    return chunks


# ---------------------------------------------------------------------------
# Query C — Recuperar tabla completa (incluyendo partes divididas)
# ---------------------------------------------------------------------------

def get_table(nombre_tabla: str) -> list[dict]:
    """
    Devuelve todos los chunks de una tabla, ordenados por parte_tabla.

    Para tablas divididas (es_tabla_dividida=true) recupera todas las partes.

    Args:
        nombre_tabla: Nombre exacto de la tabla (ej: "Tabla 4", "Tabla 3").

    Returns:
        Lista de payloads en orden de parte_tabla.
    """
    results, _ = qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="tabla",
                    match=models.MatchValue(value=nombre_tabla),
                )
            ]
        ),
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    chunks = [p.payload for p in results]
    chunks.sort(key=lambda c: c.get("parte_tabla") or 0)
    return chunks


# ---------------------------------------------------------------------------
# Query D — Expansión de contexto circundante
# ---------------------------------------------------------------------------

def get_context_window(chunk_id: str, window: int = 2) -> list[dict]:
    """
    Recupera los chunks circundantes a un chunk_id dado por proximidad de página.

    Estrategia:
      1. Localiza el chunk por chunk_id para obtener su rango de páginas.
      2. Recupera todos los chunks cuya pagina_inicio cae dentro de
         [pagina_inicio - window, pagina_fin + window].
      3. Excluye términos de glosario (son atómicos, sin contexto útil).

    Args:
        chunk_id: ID del chunk central (cualquier formato, incluido sec_slug_N_M).
        window:   Páginas en cada dirección. Default: 2.

    Returns:
        Lista de payloads ordenados por (pagina_inicio, chunk_id).
        Devuelve lista vacía si el chunk_id no existe o es de glosario.
    """
    target_results, _ = qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="chunk_id",
                    match=models.MatchValue(value=chunk_id),
                )
            ]
        ),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if not target_results:
        return []

    target = target_results[0].payload
    if target.get("tipo_contenido") == "glossary_term":
        return []

    p_ini = target.get("pagina_inicio") or 0
    p_fin = target.get("pagina_fin") or p_ini
    page_from = max(1, p_ini - window)
    page_to = p_fin + window

    results, _ = qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="pagina_inicio",
                    range=models.Range(gte=page_from, lte=page_to),
                ),
            ],
            must_not=[
                models.FieldCondition(
                    key="tipo_contenido",
                    match=models.MatchValue(value="glossary_term"),
                ),
            ],
        ),
        limit=30,
        with_payload=True,
        with_vectors=False,
    )
    chunks = [p.payload for p in results]
    chunks.sort(key=lambda c: (c.get("pagina_inicio") or 0, c.get("chunk_id", "")))
    return chunks


# ---------------------------------------------------------------------------
# Query E — Búsqueda en glosario
# ---------------------------------------------------------------------------

def glossary_search(termino: str) -> list[dict]:
    """
    Busca la definición de un término en el glosario (Anexo 5).

    Estrategia: primero intenta coincidencia exacta por termino_glosario;
    si no encuentra, hace búsqueda case-insensitive en todos los términos
    del glosario comparando en Python.

    Args:
        termino: Término a buscar (ej: "ransomware", "APT", "phishing").

    Returns:
        Lista de hasta 3 payloads de chunks de glosario.
    """
    termino_lower = termino.lower().strip()

    results, _ = qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="tipo_contenido",
                    match=models.MatchValue(value="glossary_term"),
                )
            ]
        ),
        limit=60,
        with_payload=True,
        with_vectors=False,
    )
    all_glossary = [p.payload for p in results]

    exactos = [
        c for c in all_glossary
        if termino_lower == (c.get("termino_glosario") or "").lower()
    ]
    if exactos:
        return exactos[:3]

    parciales = [
        c for c in all_glossary
        if termino_lower in (c.get("termino_glosario") or "").lower()
        or termino_lower in c.get("texto", "").lower()
    ]
    return parciales[:3]
