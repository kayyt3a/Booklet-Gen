"""Text extraction for source documents.

Strategy for PDFs:
1. Try pypdf per page (fast, works for text-based PDFs).
2. If a page extracts less than MIN_CHARS_PER_PAGE, fall back to OCR for
   just that page. Mixed PDFs (some text, some scans) get the best of both.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

MIN_CHARS_PER_PAGE = 100


def read_text(path: Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        return _read_pdf(path)
    raise ValueError(f"Unsupported source file type: {suffix} ({path})")


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))

    page_texts: list[str] = []
    ocr_needed: list[int] = []
    for i, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if len(text) < MIN_CHARS_PER_PAGE:
            page_texts.append("")
            ocr_needed.append(i)
        else:
            page_texts.append(text)

    if ocr_needed:
        log.info(
            "pdf.ocr_fallback",
            extra={"pdf": path.name, "total_pages": len(page_texts),
                   "ocr_pages": len(ocr_needed)},
        )
        from .ocr import ocr_pages
        ocr_results = ocr_pages(path, ocr_needed)
        for idx, text in zip(ocr_needed, ocr_results):
            page_texts[idx] = text

    joined = "\n\n".join(t for t in page_texts if t)
    log.info(
        "pdf.read",
        extra={"pdf": path.name, "pages": len(page_texts), "chars": len(joined)},
    )
    return joined
