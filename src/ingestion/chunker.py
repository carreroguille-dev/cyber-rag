from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

import tiktoken

# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

ContentType = Literal[
    "narrative",
    "table",
    "procedure",
    "criteria_list",
    "glossary_term",
    "legal_reference",
]

Ambito = Literal[
    "general",
    "sector_publico",
    "infraestructuras_criticas",
    "sector_privado",
    "defensa",
]

# ---------------------------------------------------------------------------
# Dataclass Chunk
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    chunk_id: str
    seccion: str
    titulo_seccion: str
    pagina_inicio: int
    pagina_fin: int
    tipo_contenido: ContentType
    texto: str

    subseccion: str | None = None
    tabla: str | None = None
    es_tabla_dividida: bool = False
    parte_tabla: int | None = None
    total_partes_tabla: int | None = None

    termino_glosario: str | None = None
    categoria_glosario: str | None = None

    referencias_cruzadas: list[str] = field(default_factory=list)
    terminos_clave: list[str] = field(default_factory=list)
    ambito: Ambito = "general"
    tokens_aproximados: int = 0

    def to_payload(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "chunk_version": "2.0",
            "seccion": self.seccion,
            "subseccion": self.subseccion,
            "titulo_seccion": self.titulo_seccion,
            "pagina_inicio": self.pagina_inicio,
            "pagina_fin": self.pagina_fin,
            "tipo_contenido": self.tipo_contenido,
            "ambito": self.ambito,
            "tabla": self.tabla,
            "es_tabla_dividida": self.es_tabla_dividida,
            "parte_tabla": self.parte_tabla,
            "total_partes_tabla": self.total_partes_tabla,
            "termino_glosario": self.termino_glosario,
            "categoria_glosario": self.categoria_glosario,
            "texto": self.texto,
            "tokens_aproximados": self.tokens_aproximados,
            "referencias_cruzadas": self.referencias_cruzadas,
            "terminos_clave": self.terminos_clave,
        }


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

MAX_TOKENS = int(__import__("os").getenv("CHUNK_MAX_TOKENS", "400"))
OVERLAP_TOKENS = 50
_TOKENIZER = tiktoken.get_encoding("cl100k_base")

_GLOSSARY_DENSITY_THRESHOLD = 0.25

_GLOSSARY_ENTRY_RE = re.compile(
    r"^[-\s]*\*{1,2}([^*\n]{1,120})\*{1,2}:?\s*(.+)", re.MULTILINE
)
_TABLE_ROW_RE = re.compile(r"^\|.+\|", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\|[-:| ]+\|", re.MULTILINE)
_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)", re.MULTILINE)

_SECTION_NUM_RE = re.compile(
    r"^(?:ANEXO\s+(\d+)\b|(\d+(?:\.\d+)*)\.?\s+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Extracción de número de sección desde el heading
# ---------------------------------------------------------------------------

def _extract_section_fields(heading: str) -> tuple[str | None, str | None]:
    """
    Extrae (seccion, subseccion) desde el texto del heading.

    Ejemplos:
      "6.1. CRITERIOS..."        → ("6",  "6.1")
      "6.1.1. Nivel..."          → ("6",  "6.1.1")
      "8 MÉTRICAS..."            → ("8",  None)
      "ANEXO 1. NOTIFICACIÓN..." → ("A1", None)
      "ALCANCE"                  → (None, None)
    """
    m = _SECTION_NUM_RE.match(heading.strip())
    if not m:
        return None, None

    if m.group(1):
        return f"A{m.group(1)}", None

    full_num = m.group(2).rstrip(".")
    if "." in full_num:
        top = full_num.split(".")[0]
        return top, full_num

    return full_num, None


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def build_chunks(pages: list[tuple[int, str]]) -> list[Chunk]:
    """
    Construye chunks a partir de las páginas en markdown producidas por ocr.py.

    Args:
        pages: Lista de (page_num_1indexed, markdown_text) ordenada por página.

    Returns:
        Lista de Chunk listos para indexar.
    """
    full_md = _assemble_with_page_markers(pages)
    sections = _split_by_headings(full_md)

    chunks: list[Chunk] = []
    sec_counter: dict[str, int] = {}

    for sec in sections:
        sec_slug = _slugify(sec["heading"])
        sec_counter[sec_slug] = sec_counter.get(sec_slug, 0) + 1
        sec_id = sec_slug if sec_counter[sec_slug] == 1 else f"{sec_slug}_{sec_counter[sec_slug]}"

        body = sec["body"]
        p_ini = sec["page_start"]
        p_fin = sec["page_end"]
        heading = sec["heading"]
        level = sec["level"]

        sec_num, subsec_num = _extract_section_fields(heading)
        seccion_field = sec_num or sec_id
        subseccion_field = subsec_num

        if _is_glossary(body):
            chunks.extend(_chunk_glossary(body, sec_id, heading, p_ini, p_fin, seccion_field))
        elif _contains_table(body):
            chunks.extend(_chunk_with_tables(body, sec_id, heading, p_ini, p_fin, seccion_field, subseccion_field))
        else:
            tipo: ContentType = "narrative"
            if level == 3:
                tipo = "procedure"
            chunks.extend(_chunk_narrative(body, sec_id, heading, p_ini, p_fin, tipo, seccion_field, subseccion_field))

    return chunks


# ---------------------------------------------------------------------------
# Ensamblado y split de secciones
# ---------------------------------------------------------------------------

def _assemble_with_page_markers(pages: list[tuple[int, str]]) -> str:
    """Une las páginas con marcadores de página para rastrear origen."""
    parts = []
    for page_num, md in pages:
        parts.append(f"<!-- page {page_num} -->")
        parts.append(md)
    return "\n\n".join(parts)


def _split_by_headings(md: str) -> list[dict]:
    """
    Divide el markdown en secciones basándose en headings de nivel 1 y 2.
    Nivel 3 (###) se trata como sub-sección dentro del body, no como split.

    Returns:
        Lista de dicts con keys: heading, level, body, page_start, page_end.
    """
    splits = []
    for m in _HEADING_RE.finditer(md):
        level = len(m.group(1))
        if level <= 2:
            splits.append((m.start(), level, m.group(2).strip()))

    if not splits:
        return [_make_section("", 1, md)]

    sections = []
    for i, (start, level, heading) in enumerate(splits):
        end = splits[i + 1][0] if i + 1 < len(splits) else len(md)
        body = md[start:end]
        body = re.sub(r"^#{1,2}\s+.+\n?", "", body, count=1)
        sections.append(_make_section(heading, level, body))

    if splits[0][0] > 0:
        preamble = md[: splits[0][0]].strip()
        if preamble:
            sections.insert(0, _make_section("Preámbulo", 1, preamble))

    return sections


def _make_section(heading: str, level: int, body: str) -> dict:
    """Crea un dict de sección con los números de página extraídos del body."""
    page_nums = [int(m.group(1)) for m in _PAGE_MARKER_RE.finditer(body)]
    clean_body = _PAGE_MARKER_RE.sub("", body).strip()
    return {
        "heading": heading,
        "level": level,
        "body": clean_body,
        "page_start": page_nums[0] if page_nums else 0,
        "page_end": page_nums[-1] if page_nums else 0,
    }


# ---------------------------------------------------------------------------
# Chunking de narrativa (con límite de tokens)
# ---------------------------------------------------------------------------

def _chunk_narrative(
    body: str,
    sec_id: str,
    heading: str,
    p_ini: int,
    p_fin: int,
    tipo: ContentType = "narrative",
    seccion_field: str | None = None,
    subseccion_field: str | None = None,
) -> list[Chunk]:
    """
    Si el body cabe en MAX_TOKENS, devuelve un único chunk.
    Si no, divide primero por H3, luego por ventana deslizante con solapamiento.
    """
    seccion_val = seccion_field or sec_id

    h3_parts = re.split(r"(?=^###\s)", body, flags=re.MULTILINE)
    h3_parts = [p.strip() for p in h3_parts if p.strip()]

    if len(h3_parts) <= 1:
        h3_parts = [body]

    chunks = []
    for part_idx, part in enumerate(h3_parts):
        h3_match = re.match(r"^###\s+(.+)\n?", part)
        subsec_title = h3_match.group(1).strip() if h3_match else None
        text = re.sub(r"^###\s+.+\n?", "", part, count=1).strip() if h3_match else part

        subseccion_val = subseccion_field or subsec_title

        windows = _token_windows(text)
        for win_idx, window_text in enumerate(windows):
            chunk_id = f"{sec_id}_{part_idx}_{win_idx}" if len(windows) > 1 else f"{sec_id}_{part_idx}"
            chunks.append(Chunk(
                chunk_id=chunk_id,
                seccion=seccion_val,
                titulo_seccion=f"{heading} — {subsec_title}" if subsec_title else heading,
                subseccion=subseccion_val,
                pagina_inicio=p_ini,
                pagina_fin=p_fin,
                tipo_contenido=tipo,
                texto=window_text,
                terminos_clave=_extract_keywords(window_text),
                tokens_aproximados=_count_tokens(window_text),
            ))

    return chunks


def _token_windows(text: str) -> list[str]:
    """Divide texto en ventanas de MAX_TOKENS con OVERLAP_TOKENS de solapamiento."""
    tokens = _TOKENIZER.encode(text)
    if len(tokens) <= MAX_TOKENS:
        return [text]

    windows = []
    start = 0
    while start < len(tokens):
        end = min(start + MAX_TOKENS, len(tokens))
        window_tokens = tokens[start:end]
        windows.append(_TOKENIZER.decode(window_tokens))
        if end == len(tokens):
            break
        start = end - OVERLAP_TOKENS

    return windows


# ---------------------------------------------------------------------------
# Chunking de tablas
# ---------------------------------------------------------------------------

def _contains_table(text: str) -> bool:
    return bool(_TABLE_SEP_RE.search(text))


def _chunk_with_tables(
    body: str,
    sec_id: str,
    heading: str,
    p_ini: int,
    p_fin: int,
    seccion_field: str | None = None,
    subseccion_field: str | None = None,
) -> list[Chunk]:
    """
    Divide el body en segmentos: texto narrativo y bloques de tabla.
    Cada tabla se convierte en un chunk independiente.
    El texto narrativo antes/después de cada tabla también se chunquea.
    """
    seccion_val = seccion_field or sec_id
    table_spans = _find_table_spans(body)

    if not table_spans:
        return _chunk_narrative(body, sec_id, heading, p_ini, p_fin,
                                seccion_field=seccion_val, subseccion_field=subseccion_field)

    chunks = []
    prev_end = 0
    table_counter = 0

    for t_start, t_end in table_spans:
        pre = body[prev_end:t_start].strip()
        if pre:
            chunks.extend(_chunk_narrative(
                pre, f"{sec_id}_pre{table_counter}", heading, p_ini, p_fin,
                seccion_field=seccion_val, subseccion_field=subseccion_field,
            ))

        table_text = body[t_start:t_end].strip()
        caption, new_end = _extract_table_caption(body, t_start, t_end)
        table_counter += 1
        chunk_id = f"{sec_id}_tabla{table_counter}"

        chunks.append(Chunk(
            chunk_id=chunk_id,
            seccion=seccion_val,
            subseccion=subseccion_field,
            titulo_seccion=caption or heading,
            pagina_inicio=p_ini,
            pagina_fin=p_fin,
            tipo_contenido="table",
            texto=table_text,
            tabla=caption,
            terminos_clave=_extract_keywords(table_text),
            tokens_aproximados=_count_tokens(table_text),
        ))
        prev_end = new_end

    post = body[prev_end:].strip()
    if post:
        chunks.extend(_chunk_narrative(
            post, f"{sec_id}_post", heading, p_ini, p_fin,
            seccion_field=seccion_val, subseccion_field=subseccion_field,
        ))

    return chunks


def _find_table_spans(text: str) -> list[tuple[int, int]]:
    """
    Devuelve lista de (start, end) para cada bloque de tabla en el texto.
    Un bloque de tabla es un grupo de líneas consecutivas que empiezan con '|'.
    """
    lines = text.split("\n")
    spans = []
    in_table = False
    t_start_char = 0
    char_pos = 0

    for line in lines:
        is_table_line = bool(re.match(r"^\|", line))
        if is_table_line and not in_table:
            in_table = True
            t_start_char = char_pos
        elif not is_table_line and in_table:
            in_table = False
            spans.append((t_start_char, char_pos))
        char_pos += len(line) + 1

    if in_table:
        spans.append((t_start_char, char_pos))

    return spans


_CAPTION_RE = re.compile(r"^\*{0,2}((Tabla|Cuadro)\s+\d+)", re.IGNORECASE)


def _extract_table_caption(
    body: str, table_start: int, table_end: int | None = None
) -> tuple[str | None, int]:
    """
    Busca el título de la tabla antes o después del bloque de tabla.

    Returns:
        (normalized_caption, new_end) where new_end is table_end advanced past
        the caption line when the caption was found after the table; otherwise
        equals table_end (or table_start if table_end is None).

    The normalized caption is always "Tabla N" / "Cuadro N" — the description
    is stripped so that get_table("Tabla 5") can match with MatchValue.
    """
    end_pos = table_end if table_end is not None else table_start

    before = body[:table_start].rstrip()
    last_line = before.split("\n")[-1].strip() if before else ""
    m = _CAPTION_RE.match(last_line)
    if m:
        return m.group(1), end_pos

    if table_end is not None:
        after_raw = body[table_end:]
        after_stripped = after_raw.lstrip("\n")
        leading_newlines = len(after_raw) - len(after_stripped)
        first_line = after_stripped.split("\n")[0].strip() if after_stripped else ""
        m = _CAPTION_RE.match(first_line)
        if m:
            line_end = after_raw.find("\n", leading_newlines)
            new_end = table_end + (line_end + 1 if line_end >= 0 else len(after_raw))
            return m.group(1), new_end

    return None, end_pos


# ---------------------------------------------------------------------------
# Chunking de glosario
# ---------------------------------------------------------------------------

def _is_glossary(text: str) -> bool:
    """Detecta si la sección es un glosario por densidad de entradas **Término**: def."""
    non_empty_lines = [l for l in text.split("\n") if l.strip()]
    if not non_empty_lines:
        return False
    matches = len(_GLOSSARY_ENTRY_RE.findall(text))
    return matches / max(len(non_empty_lines), 1) >= _GLOSSARY_DENSITY_THRESHOLD


def _chunk_glossary(
    body: str,
    sec_id: str,
    heading: str,
    p_ini: int,
    p_fin: int,
    seccion_field: str | None = None,
) -> list[Chunk]:
    """Un chunk por entrada de glosario detectada."""
    seccion_val = seccion_field or sec_id
    chunks = []
    for m in _GLOSSARY_ENTRY_RE.finditer(body):
        term = m.group(1).strip()
        definition = m.group(2).strip()
        term_id = re.sub(r"[^a-z0-9]", "_", _slugify(term))
        chunk_id = f"glosario.{term_id}"

        chunks.append(Chunk(
            chunk_id=chunk_id,
            seccion=seccion_val,
            titulo_seccion=f"Glosario — {term}",
            pagina_inicio=p_ini,
            pagina_fin=p_fin,
            tipo_contenido="glossary_term",
            texto=f"{term}: {definition}",
            termino_glosario=term,
            terminos_clave=[term],
            tokens_aproximados=_count_tokens(f"{term}: {definition}"),
        ))

    return chunks


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _count_tokens(text: str) -> int:
    return len(_TOKENIZER.encode(text))


def _slugify(text: str) -> str:
    """Convierte un heading en un identificador corto en minúsculas."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return text.strip("_")[:40] or "sec"


def _extract_keywords(text: str, top_n: int = 8) -> list[str]:
    """
    Extrae palabras clave simples por frecuencia, ignorando stopwords comunes.
    Sin dependencias externas (sin sklearn, sin nltk).
    """
    _STOPWORDS = {
        "de", "la", "el", "en", "y", "a", "los", "las", "un", "una",
        "que", "se", "por", "con", "del", "al", "es", "su", "para",
        "son", "o", "no", "lo", "le", "si", "más", "como", "pero",
        "sus", "este", "esta", "estos", "estas", "ese", "esa",
        "the", "of", "and", "in", "to", "a", "is", "for", "on",
    }
    words = re.findall(r"\b[a-záéíóúüñA-ZÁÉÍÓÚÜÑ]{3,}\b", text)
    freq: dict[str, int] = {}
    for w in words:
        w_low = w.lower()
        if w_low not in _STOPWORDS:
            freq[w_low] = freq.get(w_low, 0) + 1

    sorted_words = sorted(freq, key=lambda w: freq[w], reverse=True)
    return sorted_words[:top_n]
