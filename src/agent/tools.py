import json

from src.retrieval.qdrant_client import (
    hybrid_search,
    get_section,
    get_table,
    get_context_window,
    glossary_search,
)

# ---------------------------------------------------------------------------
# Definiciones de tools (formato OpenAI)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "hybrid_search",
            "description": (
                "Realiza una búsqueda híbrida (semántica + léxica BM25) en el documento. "
                "Devuelve los chunks más relevantes para la query. "
                "Usar como primera acción para la mayoría de consultas. "
                "Especialmente útil para preguntas sobre procedimientos, organismos, "
                "criterios y conceptos generales."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Términos de búsqueda en español. Puede ser la pregunta "
                            "del usuario o una sub-query más específica."
                        ),
                    },
                    "k": {
                        "type": "integer",
                        "description": (
                            "Número de chunks a recuperar. Default: 5. "
                            "Aumentar a 8-10 para preguntas amplias."
                        ),
                        "default": 5,
                    },
                    "filtro_seccion": {
                        "type": "string",
                        "description": (
                            "Opcional. Filtrar por sección del documento "
                            "(ej: '6', 'A1'). Usar cuando se conoce la sección relevante."
                        ),
                    },
                    "filtro_tipo": {
                        "type": "string",
                        "enum": [
                            "narrative",
                            "table",
                            "procedure",
                            "criteria_list",
                            "glossary_term",
                            "legal_reference",
                        ],
                        "description": "Opcional. Filtrar por tipo de contenido.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_table",
            "description": (
                "Recupera el contenido completo de una tabla específica del documento. "
                "Las tablas disponibles son:\n"
                "- Tabla 1: Autoridad Competente por tipo de operador\n"
                "- Tabla 2: CSIRT de referencia por tipo de operador\n"
                "- Tabla 3: Clasificación/Taxonomía de ciberincidentes (puede estar en partes)\n"
                "- Tabla 4: Criterios de determinación del nivel de PELIGROSIDAD\n"
                "- Tabla 5: Criterios de determinación del nivel de IMPACTO\n"
                "- Tabla 6: Información a notificar en un ciberincidente\n"
                "- Tabla 7: Ventana temporal de reporte (plazos de notificación)\n"
                "- Tabla 8: Estados de los ciberincidentes\n"
                "- Tabla 9: Tiempos de cierre del ciberincidente sin respuesta\n"
                "- Tabla 10-13: Métricas e indicadores (M1 a M6)\n"
                "Usar cuando la pregunta involucra clasificaciones, niveles, "
                "plazos o criterios."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre_tabla": {
                        "type": "string",
                        "description": "Nombre exacto de la tabla (ej: 'Tabla 4', 'Tabla 7')",
                    }
                },
                "required": ["nombre_tabla"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_section",
            "description": (
                "Recupera todos los fragmentos de una sección o subsección completa "
                "del documento. Útil cuando se necesita una visión completa de una "
                "sección, no solo un fragmento.\n"
                "Secciones disponibles: '1', '2', '3', '4', '5', '6', '7', '8', "
                "'A1' (Anexo 1 - PIC), 'A2' (Sector Público), 'A3' (Sector Privado), "
                "'A4' (Marco Regulador), 'A5' (Glosario).\n"
                "Subsecciones: '6.1', '6.2', '7.1', etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "seccion_id": {
                        "type": "string",
                        "description": "ID de sección ('6') o subsección ('6.1')",
                    }
                },
                "required": ["seccion_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_context_window",
            "description": (
                "Recupera los chunks circundantes a un chunk específico para obtener "
                "contexto adicional. Útil cuando un chunk contiene una referencia "
                "cruzada o cuando se necesita el contexto antes/después de un "
                "fragmento específico."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "ID del chunk central, tal como aparece en el campo chunk_id de los resultados de hybrid_search.",
                    },
                    "window": {
                        "type": "integer",
                        "description": (
                            "Número de chunks a recuperar en cada dirección. Default: 2."
                        ),
                        "default": 2,
                    },
                },
                "required": ["chunk_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glossary_lookup",
            "description": (
                "Busca la definición de uno o varios términos técnicos en el glosario "
                "de la guía (Anexo 5). "
                "Términos disponibles incluyen: malware, ransomware, APT, phishing, "
                "DoS, DDoS, rootkit, botnet, C&C, XSS, SQLi, CSIRT, y ~35 términos más. "
                "Usar cuando la pregunta es sobre el significado de un término técnico."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "termino": {
                        "type": "string",
                        "description": (
                            "Término a buscar en el glosario "
                            "(ej: 'ransomware', 'APT', 'phishing')"
                        ),
                    }
                },
                "required": ["termino"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Formateador de chunks para el contexto del agente
# ---------------------------------------------------------------------------

def _formatear_chunks(chunks: list[dict]) -> str:
    """
    Convierte una lista de payloads de chunks en texto legible
    para insertar en el historial del agente como resultado de tool.
    """
    if not chunks:
        return "No se encontraron resultados para esta búsqueda."

    partes = []
    for c in chunks:
        chunk_id = c.get("chunk_id", "?")
        seccion = c.get("titulo_seccion", c.get("seccion", ""))
        p_ini = c.get("pagina_inicio", "")
        p_fin = c.get("pagina_fin", "")
        paginas = f"p. {p_ini}" if p_ini == p_fin else f"pp. {p_ini}-{p_fin}"
        tabla = f" | {c['tabla']}" if c.get("tabla") else ""
        parte = f" (parte {c['parte_tabla']})" if c.get("es_tabla_dividida") else ""

        cabecera = f"[Chunk {chunk_id} | {seccion}{tabla}{parte} | {paginas}]"
        partes.append(f"{cabecera}\n{c.get('texto', '')}")

    return "\n\n---\n\n".join(partes)


# ---------------------------------------------------------------------------
# Despachador de tools
# ---------------------------------------------------------------------------

async def execute_tool(tool_name: str, tool_args: dict) -> tuple[str, dict]:
    """
    Ejecuta una tool y devuelve (texto_resultado, metadata_parcial).

    metadata_parcial: información para rastrear chunks_fuente, tablas, páginas.
    """
    meta: dict = {
        "chunks_ids": [],
        "tablas": [],
        "paginas": [],
        "secciones": [],
    }

    if tool_name == "hybrid_search":
        chunks = await hybrid_search(
            query=tool_args["query"],
            k=tool_args.get("k", 5),
            filtro_seccion=tool_args.get("filtro_seccion"),
            filtro_tipo=tool_args.get("filtro_tipo"),
        )

    elif tool_name == "get_table":
        nombre = tool_args["nombre_tabla"]
        chunks = get_table(nombre)
        meta["tablas"].append(nombre)

    elif tool_name == "get_section":
        chunks = get_section(tool_args["seccion_id"])

    elif tool_name == "get_context_window":
        chunks = get_context_window(
            chunk_id=tool_args["chunk_id"],
            window=tool_args.get("window", 2),
        )

    elif tool_name == "glossary_lookup":
        chunks = glossary_search(tool_args["termino"])

    else:
        return f"Tool desconocida: {tool_name}", meta

    for c in chunks:
        if cid := c.get("chunk_id"):
            meta["chunks_ids"].append(cid)
        if tabla := c.get("tabla"):
            if tabla not in meta["tablas"]:
                meta["tablas"].append(tabla)
        if p := c.get("pagina_inicio"):
            meta["paginas"].append(p)
        if p := c.get("pagina_fin"):
            meta["paginas"].append(p)
        if s := c.get("seccion"):
            if s not in meta["secciones"]:
                meta["secciones"].append(s)

    return _formatear_chunks(chunks), meta
