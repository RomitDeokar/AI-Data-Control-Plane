"""Quality gates — the promotion decision engine.

Every dataset version must pass ALL gates before it can be promoted to
production. Failed versions are rejected (data stays in staging + quarantine)
and an alert fires. This is what separates a control plane from a pipeline:
nothing reaches production without passing explicit, auditable checks.
"""

from __future__ import annotations

import logging
from typing import Any

from controlplane.config import settings
from controlplane.models import GateVerdict, QualityCheckResult

logger = logging.getLogger(__name__)


class QualityGateRunner:
    """Runs the configured gate suite against a processed dataset version."""

    def __init__(
        self,
        completeness_min: float | None = None,
        uniqueness_min: float | None = None,
        embedding_coverage_min: float | None = None,
        min_records: int | None = None,
    ):
        self.completeness_min = completeness_min or settings.gate_completeness_min
        self.uniqueness_min = uniqueness_min or settings.gate_uniqueness_min
        self.embedding_coverage_min = (
            embedding_coverage_min or settings.gate_embedding_coverage_min
        )
        self.min_records = min_records or settings.gate_min_records

    # ------------------------------------------------------------------ public
    def run_all(
        self,
        version_id: str,
        records: list[dict[str, Any]],
        required_fields: list[str],
        key_field: str = "id",
        validation_pass_rate: float = 1.0,
        embedding_coverage: float | None = None,
        drift_report: dict[str, Any] | None = None,
    ) -> GateVerdict:
        checks = [
            self._gate_min_records(records),
            self._gate_completeness(records, required_fields),
            self._gate_uniqueness(records, key_field),
            self._gate_validation_pass_rate(validation_pass_rate),
        ]
        if embedding_coverage is not None:
            checks.append(self._gate_embedding_coverage(embedding_coverage))
        if drift_report is not None:
            checks.append(self._gate_schema_drift(drift_report))

        verdict = GateVerdict(
            version_id=version_id,
            passed=all(c.passed for c in checks),
            checks=checks,
        )
        logger.info(
            "quality gates for %s: %s (%d/%d passed)",
            version_id,
            "PASS" if verdict.passed else "FAIL",
            sum(c.passed for c in checks),
            len(checks),
        )
        return verdict

    # ------------------------------------------------------------------- gates
    def _gate_min_records(self, records: list[dict[str, Any]]) -> QualityCheckResult:
        count = len(records)
        return QualityCheckResult(
            check_name="min_records",
            passed=count >= self.min_records,
            score=float(count),
            threshold=float(self.min_records),
            details={"record_count": count},
        )

    def _gate_completeness(
        self, records: list[dict[str, Any]], required_fields: list[str]
    ) -> QualityCheckResult:
        """Fraction of (record, required_field) cells that are populated."""
        if not records or not required_fields:
            return QualityCheckResult("completeness", True, 1.0, self.completeness_min)
        total_cells = len(records) * len(required_fields)
        filled = sum(
            1
            for rec in records
            for f in required_fields
            if rec.get(f) not in (None, "", [])
        )
        score = filled / total_cells
        return QualityCheckResult(
            check_name="completeness",
            passed=score >= self.completeness_min,
            score=score,
            threshold=self.completeness_min,
            details={"filled_cells": filled, "total_cells": total_cells},
        )

    def _gate_uniqueness(
        self, records: list[dict[str, Any]], key_field: str
    ) -> QualityCheckResult:
        if not records:
            return QualityCheckResult("uniqueness", True, 1.0, self.uniqueness_min)
        keys = [str(rec.get(key_field)) for rec in records if rec.get(key_field) is not None]
        if not keys:
            return QualityCheckResult(
                "uniqueness", False, 0.0, self.uniqueness_min,
                details={"error": f"no records contain key field '{key_field}'"},
            )
        score = len(set(keys)) / len(keys)
        return QualityCheckResult(
            check_name="uniqueness",
            passed=score >= self.uniqueness_min,
            score=score,
            threshold=self.uniqueness_min,
            details={"unique_keys": len(set(keys)), "total_keys": len(keys)},
        )

    def _gate_validation_pass_rate(self, pass_rate: float) -> QualityCheckResult:
        return QualityCheckResult(
            check_name="validation_pass_rate",
            passed=pass_rate >= self.completeness_min,
            score=pass_rate,
            threshold=self.completeness_min,
            details={},
        )

    def _gate_embedding_coverage(self, coverage: float) -> QualityCheckResult:
        return QualityCheckResult(
            check_name="embedding_coverage",
            passed=coverage >= self.embedding_coverage_min,
            score=coverage,
            threshold=self.embedding_coverage_min,
            details={},
        )

    def _gate_schema_drift(self, drift_report: dict[str, Any]) -> QualityCheckResult:
        drifted = bool(drift_report.get("drifted", False))
        return QualityCheckResult(
            check_name="schema_drift",
            passed=not drifted,
            score=0.0 if drifted else 1.0,
            threshold=1.0,
            details=drift_report,
        )
