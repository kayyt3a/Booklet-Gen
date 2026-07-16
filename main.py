"""CLI entry point for Folio, the tutoring booklet generator.

Two ways to generate a booklet:

  Product line (recommended):
    python main.py --program scholarships --year "Year 5" --name "Alex"
    python main.py --program naplan --year "Year 5" --name "Alex"
    python main.py --program accelerate --subject Maths --year "Year 5" \\
        --topic "fractions and area" --name "Alex"

  Free-text (original):
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
from booklet_gen.programs import PROGRAMS


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "booklet"


def main() -> int:
    parser = argparse.ArgumentParser(description="Folio: generate a tutoring booklet PDF")
    parser.add_argument("description", nargs="?", default=None,
                        help="Free-text topic, e.g. 'Year 8 maths, fractions and ratios'. "
                             "Omit when using --program.")
    parser.add_argument("--program", choices=list(PROGRAMS),
                        help="Booklet type: " + "; ".join(
                            f"{k} ({p.blurb})" for k, p in PROGRAMS.items()))
    parser.add_argument("--year", help="Year level, e.g. 'Year 5' (required with --program)")
    parser.add_argument("--subject", help="Subject for Academic Accelerate (Maths/English/Science)")
    parser.add_argument("--topic", help="Optional topic focus for the program")
    parser.add_argument("--name", default="Student", help="Student name")
    parser.add_argument("--questions", type=int, default=5, help="Questions per subtopic")
    parser.add_argument("--challenge", type=int, default=5,
                        help="Cumulative challenge questions at the end (0 to disable)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Subtopics generated in parallel (default 4). Lower it "
                             "if you hit API rate limits; 1 disables parallelism.")
    parser.add_argument("--out", default=None, help="Output PDF path")
    args = parser.parse_args()

    if not args.program and not args.description:
        parser.error("provide either a free-text description or --program")
    if args.program and not args.year:
        parser.error("--program requires --year")

    log_file = configure_logging()
    print(f"Logging to {log_file}")

    pipeline = BookletPipeline(
        questions_per_subtopic=args.questions,
        challenge_questions=args.challenge,
        max_workers=args.workers,
    )

    if args.program:
        data = pipeline.run_program(
            args.program, args.year, args.name,
            subject=args.subject, topic=args.topic,
        )
        stem = f"{_slug(args.name)}-{_slug(args.program)}-{_slug(args.year)}"
    else:
        data = pipeline.run(args.description, args.name)
        stem = f"{_slug(args.name)}-{_slug(args.description)}"

    if args.out:
        out_path = Path(args.out)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = Path("output") / f"{stem}-{ts}.pdf"

    result = render_pdf(data, out_path)
    print(f"Booklet written to {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
