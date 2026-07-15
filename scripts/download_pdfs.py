#!/usr/bin/env python3
"""Download every PDF linked on a web page into a target folder.

Built for grabbing NAPLAN past papers off ACARA and syllabus PDFs off SCSA
without clicking each link, but works on any listing page.

Usage:
    python scripts/download_pdfs.py <URL> --into <folder>
    python scripts/download_pdfs.py <URL> --into <folder> --dry-run
    python scripts/download_pdfs.py <URL> --into <folder> --contains numeracy

Idempotent — files that already exist in --into are skipped. Sequential
with a small polite delay between requests.

Zero external dependencies (stdlib only).
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

# Match href="...pdf" and href='...pdf' — quote style, casing tolerant.
_HREF_RE = re.compile(
    r"""href\s*=\s*['"]([^'"#?]+?\.pdf(?:\?[^'"]*)?)['"]""",
    re.IGNORECASE,
)


def _fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def find_pdf_links(page_url: str) -> list[str]:
    """Return a de-duplicated, absolute-URL list of every PDF linked on page."""
    body = _fetch(page_url).decode("utf-8", errors="replace")
    hrefs = _HREF_RE.findall(body)
    seen: set[str] = set()
    out: list[str] = []
    for h in hrefs:
        absolute = urllib.parse.urljoin(page_url, html.unescape(h))
        if absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out


def _safe_filename(url: str) -> str:
    name = Path(urllib.parse.urlparse(url).path).name or "download.pdf"
    # Strip stray query artefacts, keep it short-ish.
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    return name[:200]


def download_one(url: str, dest: Path) -> tuple[str, int]:
    """Fetch url and write to dest. Returns (status, bytes)."""
    if dest.exists():
        return "skipped", dest.stat().st_size
    tmp = dest.with_suffix(dest.suffix + ".part")
    data = _fetch(url, timeout=120)
    tmp.write_bytes(data)
    tmp.rename(dest)
    return "downloaded", len(data)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download every PDF linked on a page into a folder",
    )
    parser.add_argument("url", help="Listing page URL to scrape for .pdf links")
    parser.add_argument("--into", required=True, type=Path,
                        help="Target folder (created if missing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan without downloading")
    parser.add_argument("--contains", default=None,
                        help="Only download URLs whose path contains this substring (case-insensitive)")
    parser.add_argument("--delay", type=float, default=0.75,
                        help="Seconds between downloads (default 0.75; be polite)")
    args = parser.parse_args()

    try:
        links = find_pdf_links(args.url)
    except urllib.error.HTTPError as e:
        print(f"Failed to fetch {args.url}: HTTP {e.code}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Failed to fetch {args.url}: {e}", file=sys.stderr)
        return 2

    if args.contains:
        needle = args.contains.lower()
        links = [u for u in links if needle in u.lower()]

    if not links:
        print("No PDF links found on that page"
              + (f" matching --contains {args.contains!r}" if args.contains else ""),
              file=sys.stderr)
        return 1

    args.into.mkdir(parents=True, exist_ok=True)
    print(f"Found {len(links)} PDF link(s). Target: {args.into}/")
    for link in links:
        name = _safe_filename(link)
        marker = "[exists]" if (args.into / name).exists() else ""
        print(f"  {name}  <- {link}  {marker}")

    if args.dry_run:
        return 0

    ok = skipped = failed = 0
    for i, link in enumerate(links, 1):
        name = _safe_filename(link)
        dest = args.into / name
        try:
            status, size = download_one(link, dest)
        except Exception as e:
            print(f"  ! {name}: FAILED — {e}", file=sys.stderr)
            failed += 1
            continue
        if status == "skipped":
            print(f"  = {name} (already have {size:,} bytes)")
            skipped += 1
        else:
            print(f"  + {name} ({size:,} bytes)")
            ok += 1
        if i < len(links):
            time.sleep(args.delay)

    print(f"\nDone. {ok} downloaded, {skipped} skipped, {failed} failed.")
    return 0 if failed == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
