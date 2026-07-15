"""Fetch a free-licensed image from Wikimedia Commons for a given query.

Uses the Commons API (no auth required, no key needed). Returns the local
path plus a short attribution string. Returns (None, None) on any failure
so the pipeline never blocks on missing images.

We deliberately filter by license and file type — only permissively-
licensed real photos, no vectors/SVGs (which pypdf/reportlab handle poorly).
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

CACHE_DIR = Path("output/images")
API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "Booklet-Gen/1.0 (educational; contact via github)"
IMAGE_WIDTH_PX = 800
TIMEOUT_S = 15

_ACCEPTABLE_MIME = {"image/jpeg", "image/png", "image/webp"}


def _cache_path(query: str, ext: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(query.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{key}.{ext}"


def _http_json(params: dict) -> dict:
    import json
    url = f"{API_URL}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_download(url: str, out: Path) -> None:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_S) as resp:
        out.write_bytes(resp.read())


def fetch_image(query: str) -> tuple[Optional[Path], Optional[str]]:
    """Search Commons for `query`, download the top acceptable result.

    Returns (path, attribution) on success, (None, None) otherwise.
    """
    if not query or not query.strip():
        return None, None
    try:
        data = _http_json({
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": "6",  # File namespace
            "gsrlimit": "5",
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiurlwidth": str(IMAGE_WIDTH_PX),
        })
    except Exception as e:
        log.warning("wikimedia.search_failed", extra={"query": query, "error": str(e)[:200]})
        return None, None

    pages = (data.get("query") or {}).get("pages") or {}
    for _, page in sorted(pages.items(), key=lambda kv: (kv[1].get("index", 999))):
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = info.get("mime", "")
        if mime not in _ACCEPTABLE_MIME:
            continue
        url = info.get("thumburl") or info.get("url")
        if not url:
            continue
        meta = info.get("extmetadata") or {}
        license_short = (meta.get("LicenseShortName") or {}).get("value", "")
        artist = (meta.get("Artist") or {}).get("value", "")
        # Skip anything without a clearly free license.
        if not license_short or "fair use" in license_short.lower():
            continue

        # Very light HTML strip for the artist field (Commons returns markup).
        import re
        artist_plain = re.sub(r"<[^>]+>", "", artist).strip() or "Unknown"

        ext = mime.split("/")[-1] if mime else "jpg"
        if ext == "jpeg":
            ext = "jpg"
        out = _cache_path(query + "|" + url, ext)
        if not out.exists():
            try:
                _http_download(url, out)
            except Exception as e:
                log.warning("wikimedia.download_failed",
                            extra={"query": query, "error": str(e)[:200]})
                continue
        attribution = f"{artist_plain} / Wikimedia Commons ({license_short})"
        log.info("wikimedia.hit",
                 extra={"query": query, "attribution": attribution, "path": str(out)})
        return out, attribution

    log.info("wikimedia.no_hit", extra={"query": query})
    return None, None
