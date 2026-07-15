"""Chunk source documents into semantic units.

Heuristics, in order of preference:
1. Split on numbered problem markers ("1.", "1)", "Question 1")
   — matches practice-exam layouts.
2. Split on double-newline paragraphs
   — matches textbook explanatory text.
3. Merge short adjacent chunks up to a target size.
4. Cap oversized chunks with a hard character limit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_CHARS = 800
MAX_CHARS = 2000
MIN_CHARS = 120


_NUMBERED_ITEM = re.compile(
    r"(?m)^\s*(?:Question\s+)?(\d+)[\.\)]\s+"
)


@dataclass
class Chunk:
    text: str
    ordinal: int


def _split_numbered(text: str) -> list[str] | None:
    matches = list(_NUMBERED_ITEM.finditer(text))
    if len(matches) < 2:
        return None
    parts: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append(text[start:end].strip())
    return [p for p in parts if p]


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _merge_small(parts: list[str]) -> list[str]:
    merged: list[str] = []
    buffer = ""
    for p in parts:
        if not buffer:
            buffer = p
        elif len(buffer) + len(p) + 2 <= TARGET_CHARS:
            buffer = f"{buffer}\n\n{p}"
        else:
            merged.append(buffer)
            buffer = p
    if buffer:
        merged.append(buffer)
    return merged


def _cap(parts: list[str]) -> list[str]:
    capped: list[str] = []
    for p in parts:
        if len(p) <= MAX_CHARS:
            capped.append(p)
            continue
        # Hard-split oversized chunks on sentence boundaries.
        pieces = re.split(r"(?<=[\.!\?])\s+", p)
        buf = ""
        for piece in pieces:
            if len(buf) + len(piece) + 1 > MAX_CHARS and buf:
                capped.append(buf)
                buf = piece
            else:
                buf = f"{buf} {piece}".strip()
        if buf:
            capped.append(buf)
    return capped


def chunk_text(text: str) -> list[Chunk]:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    parts = _split_numbered(text) or _split_paragraphs(text)
    parts = _merge_small(parts)
    parts = _cap(parts)
    parts = [p for p in parts if len(p) >= MIN_CHARS]
    return [Chunk(text=p, ordinal=i) for i, p in enumerate(parts)]
