#!/usr/bin/env python3
"""Check the fail-closed source-rights gate without external services."""
from __future__ import annotations

import csv
import importlib.util
import tempfile
from pathlib import Path

from booklet_gen.rag.rights import (
    REQUIRED_COLUMNS,
    RightsRegister,
    RightsRegisterError,
)


failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        failures.append(label)


def row(**changes) -> dict[str, str]:
    values = {column: "" for column in REQUIRED_COLUMNS}
    values.update({
        "source_id": "folio-fractions-001",
        "source_path": "Mathematics/Year 5/Folio-Original/fractions.pdf",
        "title": "Year 5 fractions skill brief",
        "rights_holder": "FolioAI",
        "source_url": "internal",
        "access_date": "2026-08-05",
        "licence_or_permission": "Original work owned by FolioAI",
        "commercial_use": "yes",
        "adaptation_allowed": "yes",
        "ai_embedding_use": "yes",
        "attribution": "Not required",
        "exclusions": "None",
        "reviewer": "Product owner",
        "review_date": "2026-08-05",
        "decision": "approved",
        "evidence_path": "internal/source-notes.md",
        "notes": "Contains no assessment excerpts",
    })
    values.update(changes)
    return values


def write_register(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


print("\nThe rights register fails closed")
print("-" * 68)
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    register_path = root / "source_rights.csv"
    write_register(register_path, [row()])
    register = RightsRegister.load(register_path)
    approved, reason = register.approved_record(
        "mathematics\\YEAR 5\\folio-original\\fractions.pdf"
    )
    check(approved is not None and approved.approved and reason == "approved",
          "an affirmative reviewed record is approved across path styles")
    missing, reason = register.approved_record(
        "Mathematics/Year 5/Folio-Original/unknown.pdf"
    )
    check(missing is None and "not present" in reason,
          "an unregistered source is blocked")

    write_register(register_path, [row(ai_embedding_use="unclear")])
    register = RightsRegister.load(register_path)
    uncertain, reason = register.approved_record(
        "Mathematics/Year 5/Folio-Original/fractions.pdf"
    )
    check(uncertain is not None and not uncertain.approved
          and "AI and embedding use" in reason,
          "ambiguous AI and embedding permission is blocked")

    write_register(register_path, [row(), row(source_id="second")])
    try:
        RightsRegister.load(register_path)
    except RightsRegisterError:
        duplicate_blocked = True
    else:
        duplicate_blocked = False
    check(duplicate_blocked, "duplicate source paths invalidate the register")

    try:
        RightsRegister.load(root / "missing.csv")
    except RightsRegisterError:
        missing_blocked = True
    else:
        missing_blocked = False
    check(missing_blocked, "a missing register blocks ingestion")

print("\nEvery migrated vector must retain approval provenance")
print("-" * 68)
migration_path = Path("scripts/migrate_rag_to_postgres.py")
spec = importlib.util.spec_from_file_location("migrate_rag", migration_path)
migration = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(migration)

approved_meta = row()
record_path = Path("rag_sources/source_rights.csv")
header = record_path.read_text(encoding="utf-8").splitlines()[0].split(",")
check(tuple(header) == REQUIRED_COLUMNS,
      "the tracked register template has every required field")

vector_metadata = {
    "source_id": "store-hash",
    "source": "fractions.pdf",
    "rights_source_id": approved_meta["source_id"],
    "rights_decision": "approved",
    "rights_review_date": approved_meta["review_date"],
}
check(migration._rights_audit([vector_metadata]) == [],
      "approved vector metadata passes the migration audit")
old_failures = migration._rights_audit([
    {"source_id": "old-hash", "source": "past-exam.pdf"},
])
check(any("rights_decision" in failure for failure in old_failures)
      and any("rights_source_id" in failure for failure in old_failures),
      "an old unlabelled vector store is blocked from production")

ingester = Path("scripts/ingest_folder.py").read_text(encoding="utf-8")
check("RightsRegister.load" in ingester and "record.vector_metadata()" in ingester,
      "folder ingestion requires a register and stamps approved metadata")
check("!/rag_sources/source_rights.csv" in Path(".gitignore").read_text(encoding="utf-8"),
      "the rights register remains tracked while raw PDFs stay ignored")

if failures:
    print(f"\n{len(failures)} FAILED")
    raise SystemExit(1)
print("\nALL SOURCE-RIGHTS CHECKS PASSED")
