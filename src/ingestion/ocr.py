from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("MARKDOWN_CACHE_DIR", "data/markdown_cache"))

OCR_PROMPT = """\
Convierte esta página de PDF a markdown limpio. Sigue estas reglas estrictamente:
- Títulos: usa # para secciones principales, ## para subsecciones, ### para sub-subsecciones
- Tablas: usa sintaxis markdown estándar (| col | col |\\n|---|---|)
- Listas: usa - para viñetas, 1. para numeradas
- Términos de glosario en negrita: **Término**: definición
- Conserva TODO el contenido textual exactamente como aparece
- Si la página contiene solo imágenes o figuras sin texto relevante, escribe: <!-- página sin texto -->
- Emite ÚNICAMENTE el markdown, sin comentarios ni explicaciones adicionales"""


async def page_to_markdown(
    client: AsyncOpenAI,
    page_num: int,
    png_bytes: bytes,
    model: str = "gpt-5.2-2025-12-11",
) -> tuple[int, str]:
    """
    Convierte una página PNG a markdown usando el modelo de visión.
    Si existe caché en disco, la usa directamente.

    Returns:
        (page_num, markdown_text)
    """
    cache_file = CACHE_DIR / f"page_{page_num:03d}.md"
    if cache_file.exists():
        logger.debug("Página %d cargada desde caché.", page_num)
        return page_num, cache_file.read_text(encoding="utf-8")

    b64 = base64.b64encode(png_bytes).decode()
    response = await client.chat.completions.create(
        model=model,
        max_completion_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": OCR_PROMPT},
                ],
            }
        ],
    )
    markdown = response.choices[0].message.content.strip()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(markdown, encoding="utf-8")
    logger.debug("Página %d procesada y guardada en caché.", page_num)

    return page_num, markdown


async def pdf_to_markdown(
    pdf_path: str,
    client: AsyncOpenAI,
    model: str = "gpt-5.2-2025-12-11",
    concurrency: int = 5,
) -> list[tuple[int, str]]:
    """
    Procesa todas las páginas del PDF concurrentemente (máx. `concurrency` simultáneas).
    Las páginas ya en caché no consumen llamadas a la API.

    Returns:
        Lista de (page_num, markdown_text) ordenada por número de página.
    """
    from src.ingestion.pdf_renderer import render_pages

    pages = render_pages(pdf_path)
    total = len(pages)
    cached = sum(1 for n, _ in pages if (CACHE_DIR / f"page_{n:03d}.md").exists())
    logger.info(
        "PDF renderizado: %d páginas (%d en caché, %d a procesar con OCR).",
        total, cached, total - cached,
    )

    sem = asyncio.Semaphore(concurrency)

    async def _process(page_num: int, png_bytes: bytes) -> tuple[int, str]:
        async with sem:
            return await page_to_markdown(client, page_num, png_bytes, model)

    results = await asyncio.gather(*[_process(n, b) for n, b in pages])
    return sorted(results, key=lambda x: x[0])
