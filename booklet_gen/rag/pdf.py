"""Text extraction for source documents. Text-based PDFs only (no OCR)."""
from __future__ import annotations

from pathlib import Path


def read_text(path: Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
        return "\n\n".join(pages)
    raise ValueError(f"Unsupported source file type: {suffix} ({path})")
