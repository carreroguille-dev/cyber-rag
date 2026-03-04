# CHUNKING STRATEGY
## Pipeline de Ingesta — OCR por Visión + Chunker Agnóstico

---

## 1. Principio Fundamental

**No hardcodear la estructura del documento. Detectarla automáticamente desde el Markdown.**

La estrategia original dividía el PDF en chunks predefinidos por sección (94 chunks hardcodeados para la estructura exacta de la Guía Nacional). Ese enfoque es frágil: cualquier actualización del PDF requiere reescribir el chunker.

La estrategia actual usa visión para convertir cada página a Markdown y luego detecta automáticamente el tipo de contenido por estructura sintáctica. El chunker es agnóstico al documento: funciona con cualquier PDF procesado por el mismo pipeline OCR.

---

## 2. Pipeline Completo

```
PDF
 └─► PyMuPDF: render página → PNG (dpi=150)
      └─► gpt-5.2-2025-12-11 vision: PNG → Markdown   [caché en disco]
           └─► Ensamblar documento completo con marcadores <!-- page N -->
                └─► Dividir en secciones H1/H2
                     └─► Detectar tipo de sección
                          ├─► Glosario  → un chunk por entrada **Término**:
                          ├─► Con tabla → chunk(s) narrativos + chunk por tabla
                          └─► Narrativo → sub-dividir en H3, luego ventana de tokens
```

---

## 3. Módulos

### `pdf_renderer.py` — PDF → PNG

```python
import fitz  # PyMuPDF

def render_pages(pdf_path: str, dpi: int = 150) -> list[tuple[int, bytes]]:
    """Devuelve lista de (num_página_1indexed, png_bytes)."""
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        pages.append((i + 1, pix.tobytes("png")))
    doc.close()
    return pages
```

`dpi=150` equilibra calidad de imagen y coste de tokens para el modelo de visión.

### `ocr.py` — PNG → Markdown con caché en disco

```python
OCR_PROMPT = """Convert this PDF page to clean markdown. Rules:
- Headings: use # for main sections, ## for subsections, ### for sub-subsections
- Tables: use standard markdown table syntax (| col | col |\n|---|---|)
- Lists: use - for bullets, 1. for numbered
- Bold terms in glossary: **Term**: definition
- Preserve ALL text content exactly as it appears
- Output ONLY the markdown, no commentary"""
```

El OCR guarda cada página como `data/markdown_cache/page_NNN.md`. En re-ingestas, las páginas cacheadas se leen desde disco — cero llamadas al modelo de visión.

Concurrencia configurable con `OCR_CONCURRENCY` (por defecto 5 páginas en paralelo).

### `chunker.py` — Markdown → objetos Chunk

**Detección de tipo de sección:**

| Criterio de detección | Tipo asignado | Estrategia de sub-chunking |
|---|---|---|
| Densidad de `**Término**:` > 25% de líneas | `glossary_term` | Un chunk por entrada `**Término**: definición` |
| Contiene `\|---\|` (tabla Markdown) | `table` + `narrative` | Un chunk por tabla + chunks narrativos adyacentes |
| En otro caso | `narrative` | Sub-dividir en H3, luego ventana deslizante 400 tok / 50 overlap |

**Puntos clave de la implementación:**

- `_assemble_with_page_markers(pages)`: inserta `<!-- page N -->` entre páginas para rastrear origen
- `_split_by_headings(md, levels=[1,2])`: divide en secciones en cada `# ` o `## ` que empieza línea
- `_extract_table_caption(body, table_start, table_end)`: busca el pie de tabla **después** del bloque `|---|` (convención española en documentos normativos) y devuelve solo `"Tabla N"` normalizado
- `_is_glossary(section)`: comprueba que `^[-\s]*\*{1,2}[^*\n]+\*{1,2}:` esté en ≥25% de las líneas
- `_chunk_glossary(section)`: regex `r"^[-\s]*\*{1,2}([^*\n]{1,120})\*{1,2}:?\s*(.+)"` — acepta el prefijo `- ` que el OCR añade a cada entrada

#### Diseño del flujo de chunkeado (visión previa)

El comportamiento deseado del chunker, antes de implementarlo, es el siguiente:

1. **Entrada del sistema**
   - **Entrada**: lista de páginas ya convertidas a Markdown por `ocr.py`, en la forma `[(número_página, markdown), ...]`, ordenadas.
   - **Salida esperada**: lista de objetos `Chunk` listos para indexar en Qdrant.

2. **Ensamblado con marcadores de página**
   - El chunker unirá todas las páginas en un único string de Markdown, insertando antes de cada página un marcador HTML del tipo `<!-- page N -->`.
   - Estos marcadores serán la única fuente de verdad para reconstruir `pagina_inicio` y `pagina_fin` de cada chunk.

3. **Split principal por headings**
   - El Markdown completo se dividirá en secciones usando únicamente headings de nivel 1 y 2 (`# ` y `## ` al inicio de línea).
   - Cada sección almacenará:
     - El texto del heading.
     - El nivel (`1` o `2`).
     - El body sin el propio heading.
     - El rango de páginas (`page_start`, `page_end`) calculado a partir de los marcadores `<!-- page N -->` presentes en el body.
   - Un posible texto previo al primer heading se tratará como sección especial "Preámbulo".

4. **Extracción de número de sección**
   - A partir del texto del heading se extraerán dos campos:
     - `seccion`: número "top" (`"6"`, `"8"`, `"A1"`, etc.).
     - `subseccion`: numeración completa cuando exista (`"6.1"`, `"6.1.1"`, etc.).
   - Si no se detecta numeración, se usará un `sec_id` derivado de un *slug* del heading (`_slugify`) como valor por defecto de `seccion`.

5. **Clasificación de la sección (tipo de contenido)**
   - Para cada sección se decidirá el tipo de chunking según este orden:
     1. Si la densidad de líneas que cumplen el patrón de glosario `**Término**: definición` es ≥ 25 % → la sección se tratará como **glosario**.
     2. En caso contrario, si el body contiene tablas Markdown (líneas `| ... |` con separadores `|---|`) → la sección se tratará como **texto con tablas**.
     3. En el resto de casos → la sección se considerará **narrativa**.
   - Adicionalmente, para headings de nivel 3 (`###`) dentro del body, el tipo podrá especializarse a `procedure` cuando represente pasos operativos.

6. **Chunking de secciones narrativas**
   - Para secciones narrativas, el diseño será:
     - Primero dividir el body por sub-headings `###` (si los hay), tratando cada bloque como sub-sección lógica.
     - Para cada bloque:
       - Si el texto cabe en `MAX_TOKENS` (configurable por `CHUNK_MAX_TOKENS`, por defecto 400) → un único chunk.
       - Si no cabe → aplicar una **ventana deslizante de tokens** con tamaño `MAX_TOKENS` y solapamiento `OVERLAP_TOKENS` (50 tokens).
     - Cada ventana generará un `Chunk` con:
       - `chunk_id` basado en el identificador de sección (`sec_id`) más índices de parte/ventana.
       - `tipo_contenido` `narrative` (o `procedure` si aplica).
       - `pagina_inicio` / `pagina_fin` heredados de la sección.
       - `terminos_clave` y `tokens_aproximados` calculados automáticamente.

7. **Chunking de secciones con tablas**
   - Para secciones que contengan tablas:
     - Se localizarán bloques contiguos de líneas que forman tablas Markdown.
     - El texto narrativo **antes** de cada bloque de tabla se chunqueará usando la misma estrategia narrativa anterior.
     - Cada bloque de tabla se convertirá en un `Chunk` independiente con:
       - `tipo_contenido = "table"`.
       - `tabla` = nombre normalizado de la tabla (`"Tabla N"` o `"Cuadro N"`), extraído del título antes o después del bloque.
       - `pagina_inicio` / `pagina_fin` de la sección.
     - El texto narrativo **después** de la última tabla también se chunqueará como narrativa.

8. **Chunking del glosario**
   - En secciones detectadas como glosario:
     - Cada línea/entrada que cumpla `**Término**: definición` generará exactamente un chunk.
     - El `chunk_id` seguirá el formato `glosario.{slug_del_termino}`.
     - `tipo_contenido` será `glossary_term`, y el campo `termino_glosario` contendrá el término original.

9. **Garantías de diseño**
   - Las tablas no se dividirán por límite de tokens: siempre se mantendrán completas en un solo chunk.
   - Las entradas de glosario serán atómicas (un término por chunk).
   - Todo chunk tendrá:
     - Un identificador estable (`chunk_id`).
     - Rango de páginas trazable al PDF original.
     - Texto autocontenido preparado para ser embebido por el modelo de embeddings.

---

## 4. Esquema de Metadatos por Chunk

```json
{
  "chunk_id":        "sec_6_3_0",
  "chunk_version":   "1.0",
  "seccion":         "6",
  "subseccion":      "6.3",
  "titulo_seccion":  "Nivel de Impacto",
  "pagina_inicio":   22,
  "pagina_fin":      24,
  "tipo_contenido":  "narrative",
  "tabla":           null,
  "es_tabla_dividida": false,
  "termino_glosario": null,
  "ambito":          "general",
  "texto":           "...[contenido completo del chunk]...",
  "terminos_clave":  ["impacto", "peligrosidad", "OSE", "PSD"]
}
```

### Formato de `chunk_id`

| Tipo de chunk | Formato | Ejemplo |
|---|---|---|
| Chunk narrativo/tabla | `sec_{seccion_idx}_{chunk_idx}` | `sec_6_3`, `sec_6_3_0` |
| Entrada de glosario | `glosario.{termino_normalizado}` | `glosario.ransomware`, `glosario.apt` |

### Campos relevantes

| Campo | Tipo | Descripción |
|---|---|---|
| `chunk_id` | string | Identificador único |
| `seccion` | string | Número de sección del heading H1/H2 detectado |
| `titulo_seccion` | string | Texto del heading |
| `pagina_inicio / pagina_fin` | int | Extraído de los marcadores `<!-- page N -->` |
| `tipo_contenido` | enum | `narrative`, `table`, `glossary_term` |
| `tabla` | string \| null | `"Tabla N"` normalizado; solo para `tipo_contenido=table` |
| `termino_glosario` | string \| null | Término para entradas de glosario |
| `ambito` | enum | `general` por defecto |
| `terminos_clave` | list[string] | Top-8 palabras clave por frecuencia en el texto del chunk |

---

## 5. Resultado de la Ingesta

| Métrica | Valor |
|---|---|
| Páginas procesadas | 55 |
| Chunks totales | 212 |
| Entradas de glosario | ~73 |
| Tablas con campo `tabla` correcto | 13/13 |
| Tamaño aprox. del índice en Qdrant | ~1.5 MB |

---

## 6. Reglas Inviolables

1. **Las tablas no se fragmentan por tokens**. Si una tabla supera el límite del contexto del LLM, se incluye completa en un único chunk. La tabla siempre tiene prioridad sobre el límite de tokens.

2. **El glosario es atómico por término**. Cada entrada `**Término**: definición` es un chunk independiente.

3. **El campo `tabla` siempre es `"Tabla N"`** (no el texto completo del pie). `get_table` filtra por `MatchValue` en Qdrant — el formato normalizado es crítico.

4. **El caché OCR no se borra entre arranques**. Re-ingestar sin borrar `data/markdown_cache/` solo llama a la API de embeddings, no al modelo de visión.

5. **Para forzar re-OCR**: `rm -rf data/markdown_cache/` antes de ejecutar `docker compose run --rm ingest`.

---

## 7. Configuración por Variables de Entorno

| Variable | Por defecto | Descripción |
|---|---|---|
| `OCR_MODEL` | `gpt-5.2-2025-12-11` | Modelo de visión para OCR |
| `OCR_CONCURRENCY` | `5` | Páginas procesadas en paralelo |
| `MARKDOWN_CACHE_DIR` | `data/markdown_cache` | Directorio de caché OCR en disco |
| `PDF_PATH` | `data/guia_nacional_notificacion_gestion_ciberincidentes.pdf` | Ruta al PDF fuente |
