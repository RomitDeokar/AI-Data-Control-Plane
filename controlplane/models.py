"""Domain models shared across the control plane."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class VersionStatus(StrEnum):
    INGESTED = "ingested"
    VALIDATED = "validated"
    ENRICHED = "enriched"
    EMBEDDED = "embedded"
    GATED = "gated"
    PROMOTED = "promoted"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class TriggerType(StrEnum):
    EVENT = "event"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    MANUAL = "manual"


def new_version_id(dataset: str) -> str:
    """Generate a sortable, human-readable version id: products-20260703-142530-a1b2."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    return f"{dataset}-{ts}-{suffix}"


def schema_fingerprint(records: list[dict[str, Any]]) -> str:
    """Stable hash of the observed field set + types, used for drift detection."""
    fields: dict[str, str] = {}
    for rec in records[:200]:  # sample for speed
        for key, value in rec.items():
            fields.setdefault(key, type(value).__name__)
    canonical = json.dumps(fields, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass
class DatasetVersion:
    dataset: str
    version_id: str
    source_uri: str
    pipeline: str
    trigger_type: str = TriggerType.MANUAL.value
    status: str = VersionStatus.INGESTED.value
    record_count: int | None = None
    schema_hash: str | None = None


@dataclass
class QualityCheckResult:
    check_name: str
    passed: bool
    score: float
    threshold: float
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check_name,
            "passed": self.passed,
            "score": round(self.score, 4),
            "threshold": self.threshold,
            "details": self.details,
        }


@dataclass
class GateVerdict:
    """Aggregate result of all quality gates for one dataset version."""

    version_id: str
    passed: bool
    checks: list[QualityCheckResult]

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "passed": self.passed,
            "total_checks": len(self.checks),
            "failed_checks": [c.check_name for c in self.checks if not c.passed],
            "checks": [c.as_dict() for c in self.checks],
        }


@dataclass
class ValidationResult:
    valid_records: list[dict[str, Any]]
    quarantined: list[dict[str, Any]]  # each: {"record": ..., "reason": ...}
    schema_hash: str

    @property
    def pass_rate(self) -> float:
        total = len(self.valid_records) + len(self.quarantined)
        return len(self.valid_records) / total if total else 0.0
