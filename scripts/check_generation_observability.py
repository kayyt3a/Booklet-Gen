"""Check that production logs expose generation stage timing and diagram errors."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} is missing from {path}")


def main() -> None:
    jobs = function_source(ROOT / "booklet_gen" / "jobs.py", "_generate")
    diagrams = function_source(
        ROOT / "booklet_gen" / "visuals" / "diagrams.py", "render_diagram")
    assert jobs.count("generation.stage_completed") >= 8
    assert "stage=pipeline duration_seconds=%.3f" in jobs
    assert "stage=render duration_seconds=%.3f" in jobs
    assert "stage=store duration_seconds=%.3f" in jobs
    assert "time.monotonic()" in jobs
    assert "diagram.render_failed type=%s error=%s" in diagrams
    print("PASS generation stages and diagram failure details are logged")


if __name__ == "__main__":
    main()
