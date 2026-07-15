"""OCR fallback for image-based PDF pages.

Uses pdf2image (poppler) to render pages, then pytesseract to OCR them.
Only invoked per-page when the pypdf text extraction is too sparse to be
useful — mixed PDFs still get fast text extraction where possible.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Render at a DPI that balances OCR quality against speed. 220 handles
# workbook-style scans reliably; higher DPI is slower with diminishing returns.
OCR_DPI = 220


def ocr_pages(pdf_path: Path, page_numbers: list[int]) -> list[str]:
    """OCR the specified 0-indexed pages of the PDF and return their text.

    Returns "" for any page where OCR fails, rather than raising, so a single
    unreadable page doesn't kill the whole ingest.
    """
    if not page_numbers:
        return []
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as e:
        log.warning("ocr.deps_missing", extra={"error": str(e)})
        return [""] * len(page_numbers)

    results: list[str] = []
    for zero_idx in page_numbers:
        one_idx = zero_idx + 1
        try:
            images = convert_from_path(
                str(pdf_path),
                dpi=OCR_DPI,
                first_page=one_idx,
                last_page=one_idx,
            )
            if not images:
                results.append("")
                continue
            text = pytesseract.image_to_string(images[0])
            results.append((text or "").strip())
            log.info(
                "ocr.page",
                extra={"pdf": pdf_path.name, "page": one_idx, "chars": len(results[-1])},
            )
        except Exception as e:
            log.warning(
                "ocr.page_failed",
                extra={"pdf": pdf_path.name, "page": one_idx, "error": str(e)[:200]},
            )
            results.append("")
    return results
