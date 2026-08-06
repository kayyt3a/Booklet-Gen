"""Rights-register checks for material entering FolioAI's vector store.

The register is deliberately plain CSV so it can be reviewed without running
the application. A source is approved only when the decision and each required
use permission are affirmative. Missing or ambiguous values fail closed.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


REQUIRED_COLUMNS = (
    "source_id",
    "source_path",
    "title",
    "rights_holder",
    "source_url",
    "access_date",
    "licence_or_permission",
    "commercial_use",
    "adaptation_allowed",
    "ai_embedding_use",
    "attribution",
    "exclusions",
    "reviewer",
    "review_date",
    "decision",
    "evidence_path",
    "notes",
)

_YES = frozenset({"yes", "true", "1"})


class RightsRegisterError(ValueError):
    """The source-rights register is missing or cannot be trusted."""


def normalise_source_path(value: str | Path) -> str:
    """Return one portable, case-insensitive key for a registered source."""
    text = str(value).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/").casefold()


@dataclass(frozen=True)
class RightsRecord:
    source_id: str
    source_path: str
    title: str
    rights_holder: str
    source_url: str
    access_date: str
    licence_or_permission: str
    commercial_use: str
    adaptation_allowed: str
    ai_embedding_use: str
    attribution: str
    exclusions: str
    reviewer: str
    review_date: str
    decision: str
    evidence_path: str
    notes: str

    @property
    def approved(self) -> bool:
        return (
            self.decision.strip().casefold() == "approved"
            and self.commercial_use.strip().casefold() in _YES
            and self.adaptation_allowed.strip().casefold() in _YES
            and self.ai_embedding_use.strip().casefold() in _YES
            and bool(self.source_id.strip())
            and bool(self.reviewer.strip())
            and bool(self.review_date.strip())
            and bool(self.licence_or_permission.strip())
        )

    @property
    def block_reason(self) -> str:
        if self.decision.strip().casefold() != "approved":
            return f"decision is {self.decision.strip() or 'blank'}"
        missing_permissions = [
            label for label, value in (
                ("commercial use", self.commercial_use),
                ("adaptation", self.adaptation_allowed),
                ("AI and embedding use", self.ai_embedding_use),
            ) if value.strip().casefold() not in _YES
        ]
        if missing_permissions:
            return "permission is not yes for " + ", ".join(missing_permissions)
        missing_review = [
            label for label, value in (
                ("source id", self.source_id),
                ("licence or permission", self.licence_or_permission),
                ("reviewer", self.reviewer),
                ("review date", self.review_date),
            ) if not value.strip()
        ]
        if missing_review:
            return "missing " + ", ".join(missing_review)
        return "not approved"

    def vector_metadata(self) -> dict[str, str]:
        """Small provenance marker copied into every vector-store chunk."""
        return {
            "rights_source_id": self.source_id.strip(),
            "rights_decision": "approved",
            "rights_review_date": self.review_date.strip(),
            "rights_licence": self.licence_or_permission.strip(),
        }


class RightsRegister:
    def __init__(self, path: Path, records: dict[str, RightsRecord]):
        self.path = path
        self._records = records

    @classmethod
    def load(cls, path: str | Path) -> "RightsRegister":
        register_path = Path(path)
        if not register_path.is_file():
            raise RightsRegisterError(
                f"Rights register not found: {register_path}. Nothing may be ingested."
            )
        with register_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headings = tuple(reader.fieldnames or ())
            missing = [column for column in REQUIRED_COLUMNS if column not in headings]
            if missing:
                raise RightsRegisterError(
                    "Rights register is missing columns: " + ", ".join(missing)
                )
            records: dict[str, RightsRecord] = {}
            source_ids: set[str] = set()
            for line_number, raw in enumerate(reader, start=2):
                if not any((value or "").strip() for value in raw.values()):
                    continue
                values = {column: (raw.get(column) or "").strip()
                          for column in REQUIRED_COLUMNS}
                record = RightsRecord(**values)
                key = normalise_source_path(record.source_path)
                if not key:
                    raise RightsRegisterError(
                        f"Rights register line {line_number} has no source_path."
                    )
                if key in records:
                    raise RightsRegisterError(
                        f"Duplicate source_path in rights register: {record.source_path}"
                    )
                source_id_key = record.source_id.casefold()
                if source_id_key and source_id_key in source_ids:
                    raise RightsRegisterError(
                        f"Duplicate source_id in rights register: {record.source_id}"
                    )
                if source_id_key:
                    source_ids.add(source_id_key)
                records[key] = record
        return cls(register_path, records)

    def find(self, source_path: str | Path) -> RightsRecord | None:
        return self._records.get(normalise_source_path(source_path))

    def approved_record(self, source_path: str | Path) -> tuple[RightsRecord | None, str]:
        record = self.find(source_path)
        if record is None:
            return None, "not present in the source-rights register"
        if not record.approved:
            return record, record.block_reason
        return record, "approved"

    def __len__(self) -> int:
        return len(self._records)
