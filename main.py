"""CLI entry point for the booklet generator.

Usage:
    python main.py "Year 8 maths, fractions and ratios" --name "Alex"
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

from booklet_gen.formatter import render_pdf
from booklet_gen.logging_setup import configure_logging
from booklet_gen.pipeline import BookletPipeline


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "booklet"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a maths tutoring booklet PDF")
    parser.add_argument("description", help="e.g. 'Year 8 maths, fractions and ratios'")
    parser.add_argument("--name", default="Student", help="Student name")
    parser.add_argument("--questions", type=int, default=5, help="Questions per subtopic")
    parser.add_argument("--challenge", type=int, default=5,
                        help="Cumulative challenge questions at the end (0 to disable)")
    parser.add_argument("--out", default=None, help="Output PDF path")
    args = parser.parse_args()

    log_file = configure_logging()
    print(f"Logging to {log_file}")

    pipeline = BookletPipeline(
        questions_per_subtopic=args.questions,
        challenge_questions=args.challenge,
    )
    data = pipeline.run(args.description, args.name)

    if args.out:
        out_path = Path(args.out)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = Path("output") / f"{_slug(args.name)}-{_slug(args.description)}-{ts}.pdf"

    result = render_pdf(data, out_path)
    print(f"Booklet written to {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
