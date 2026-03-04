from __future__ import annotations

import fitz


def render_pages(pdf_path: str, dpi: int = 150) -> list[tuple[int, bytes]]:
    """
    Renderiza cada página del PDF como PNG.

    Args:
        pdf_path: Ruta al archivo PDF.
        dpi: Resolución de renderizado. 150 equilibra calidad y coste del modelo.

    Returns:
        Lista de (page_num_1indexed, png_bytes) ordenada por página.
    """
    doc = fitz.open(pdf_path)
    pages: list[tuple[int, bytes]] = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        pages.append((i + 1, pix.tobytes("png")))

    doc.close()
    return pages
