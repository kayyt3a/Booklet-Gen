"""Fetch a free-licensed image from Wikimedia Commons for a given query.

Uses the Commons API (no auth required, no key needed). Returns the local
path plus a short attribution string. Returns (None, None) on any failure
so the pipeline never blocks on missing images.

Two filters, and they do different jobs. Licence and file type decide what we
are allowed to print: permissively licensed real photos only, no vectors or
SVGs, which pypdf and reportlab handle poorly. `query_is_refused` decides what
we are willing to go looking for at all, which copyright has nothing to say
about and which matters more, because the result is printed for a child.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
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
    url = f"{API_URL}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_download(url: str, out: Path) -> None:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_S) as resp:
        out.write_bytes(resp.read())


# Queries we will not run at all.
#
# The licence filter below is the only thing that used to stand between an
# LLM-written `image_query` and whatever Commons returns first, and it checks
# copyright, not content. This booklet goes to a child, and the query is
# written by a model that has just been told to write about Australia, so
# "Aboriginal ceremony" or "Bogong moth harvest" are queries it could plausibly
# produce. The top Commons hit for those can be a historical photograph of
# deceased Aboriginal people or material a community holds as restricted, which
# in Australia is a serious cultural breach and, for images of the deceased,
# one some communities regard as harmful to view.
#
# A picture in a FolioAI booklet is decoration for a reading passage. It is never
# worth that risk, so a query naming people, ceremony, remains or anything
# sacred is refused and the question simply prints without a picture. The
# prompts also ask for object, animal, place or machine; this is the backstop
# for when they are not followed.
_REFUSED_QUERY_TERMS = frozenset("""
    aboriginal indigenous torres first nations native tribe tribal
    ceremony ceremonial ritual sacred spiritual dreaming corroboree initiation
    burial funeral grave remains skeleton skull deceased dead body corpse
    people person child children boy girl man woman men women family portrait
    protest war soldier weapon gun rifle victim refugee patient nude
""".split())


def query_is_refused(query: str) -> bool:
    """Whether this image query must not be run. See `_REFUSED_QUERY_TERMS`."""
    words = re.findall(r"[a-z]+", (query or "").lower())
    return any(w in _REFUSED_QUERY_TERMS for w in words)


def fetch_image(query: str) -> tuple[Optional[Path], Optional[str]]:
    """Search Commons for `query`, download the top acceptable result.

    Returns (path, attribution) on success, (None, None) otherwise.
    """
    # Network imagery is external content too. It stays off in clean-room mode
    # even when a model emits image_query. Programmatic diagrams are rendered
    # by diagrams.py and do not pass through this gate.
    from ..programs import reviewed_external_content_enabled
    if not reviewed_external_content_enabled():
        log.info("wikimedia.disabled_cleanroom")
        return None, None
    if not query or not query.strip():
        return None, None
    if query_is_refused(query):
        log.info("wikimedia.query_refused", extra={"query": query})
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
