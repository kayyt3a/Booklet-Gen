"""Optional private Supabase Storage backend for generated deliverables."""
from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(
        (os.environ.get("SUPABASE_URL") or "").strip()
        and (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    )


def bucket() -> str:
    return (os.environ.get("FOLIO_STORAGE_BUCKET") or "booklets").strip()


def _base() -> str:
    return os.environ["SUPABASE_URL"].strip().rstrip("/") + "/storage/v1"


def _headers(content_type: str = "application/json") -> dict[str, str]:
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    return {
        "Authorization": f"Bearer {key}",
        "apikey": key,
        "Content-Type": content_type,
    }


def _call(request: Request, timeout: int = 30) -> bytes:
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError) as exc:
        detail = getattr(exc, "read", lambda: b"")().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Supabase Storage request failed: {exc} {detail}") from exc


def upload(key: str, data: bytes, mimetype: str) -> None:
    path = quote(key, safe="/")
    headers = _headers(mimetype)
    headers["x-upsert"] = "true"
    request = Request(
        f"{_base()}/object/{quote(bucket())}/{path}",
        data=data,
        headers=headers,
        method="POST",
    )
    _call(request, timeout=60)


def signed_download_url(key: str, expires_seconds: int = 300) -> str:
    path = quote(key, safe="/")
    request = Request(
        f"{_base()}/object/sign/{quote(bucket())}/{path}",
        data=json.dumps({"expiresIn": int(expires_seconds)}).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    payload = json.loads(_call(request).decode("utf-8"))
    signed = payload.get("signedURL") or payload.get("signedUrl")
    if not signed:
        raise RuntimeError("Supabase Storage did not return a signed URL.")
    return signed if signed.startswith("http") else _base() + signed


def delete(keys: list[str]) -> None:
    if not keys:
        return
    request = Request(
        f"{_base()}/object/{quote(bucket())}",
        data=json.dumps({"prefixes": keys}).encode("utf-8"),
        headers=_headers(),
        method="DELETE",
    )
    _call(request)
