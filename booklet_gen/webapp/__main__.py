"""Run FolioAI's local development server with ``python -m booklet_gen.webapp``."""
from __future__ import annotations

import os

from . import create_app


def main() -> None:
    host = (os.environ.get("FOLIO_HOST") or "127.0.0.1").strip()
    port = int(os.environ.get("PORT", "5000"))
    create_app().run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
