"""Postgres metadata registry — the audit trail of the control plane.

Every version, quality report, quarantined record, and promotion decision is
persisted here, making the whole system auditable and queryable.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg

from controlplane.config import settings
from controlplane.models import DatasetVersion, GateVerdict

logger = logging.getLogger(__name__)


class MetadataRegistry:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or settings.database_url

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, autocommit=True)

    # ---------------------------------------------------------------- versions
    def register_version(self, version: DatasetVersion) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO controlplane.dataset_versions
                    (dataset, version_id, source_uri, pipeline, trigger_type,
                     status, record_count, schema_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (version_id) DO NOTHING
                """,
                (
                    version.dataset,
                    version.version_id,
                    version.source_uri,
                    version.pipeline,
                    version.trigger_type,
                    version.status,
                    version.record_count,
                    version.schema_hash,
                ),
            )

    def update_version_status(
        self, version_id: str, status: str, record_count: int | None = None
    ) -> None:
        with self._connect() as conn:
            if record_count is not None:
                conn.execute(
                    """UPDATE controlplane.dataset_versions
                       SET status=%s, record_count=%s, updated_at=now()
                       WHERE version_id=%s""",
                    (status, record_count, version_id),
                )
            else:
                conn.execute(
                    """UPDATE controlplane.dataset_versions
                       SET status=%s, updated_at=now() WHERE version_id=%s""",
                    (status, version_id),
                )

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT dataset, version_id, source_uri, pipeline, trigger_type,
                          status, record_count, schema_hash, created_at, updated_at
                   FROM controlplane.dataset_versions WHERE version_id=%s""",
                (version_id,),
            ).fetchone()
        if not row:
            return None
        keys = [
            "dataset", "version_id", "source_uri", "pipeline", "trigger_type",
            "status", "record_count", "schema_hash", "created_at", "updated_at",
        ]
        return {k: (str(v) if k.endswith("_at") else v) for k, v in zip(keys, row, strict=False)}

    def list_versions(self, dataset: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = """SELECT dataset, version_id, status, record_count, trigger_type, created_at
                   FROM controlplane.dataset_versions"""
        params: tuple = ()
        if dataset:
            query += " WHERE dataset=%s"
            params = (dataset,)
        query += " ORDER BY created_at DESC LIMIT %s"
        params += (limit,)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        keys = ["dataset", "version_id", "status", "record_count", "trigger_type", "created_at"]
        return [
            {k: (str(v) if k == "created_at" else v) for k, v in zip(keys, row, strict=False)}
            for row in rows
        ]

    def get_last_promoted_schema_hash(self, dataset: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT schema_hash FROM controlplane.dataset_versions
                   WHERE dataset=%s AND status='promoted'
                   ORDER BY updated_at DESC LIMIT 1""",
                (dataset,),
            ).fetchone()
        return row[0] if row else None

    def get_previous_promoted(self, dataset: str, exclude_current: str | None) -> str | None:
        """Version id of the most recent promoted version other than current.

        ``exclude_current`` may be either a bare ``version_id`` *or* a Qdrant
        collection name (``{dataset}__{version_id}``) — the latter is what the
        vector store's ``get_alias_target`` returns. We normalise to a version
        id by stripping the ``{dataset}__`` prefix if (and only if) it is present.
        """
        collection_prefix = f"{dataset}__"
        exclude_version = ""
        if exclude_current:
            exclude_version = (
                exclude_current[len(collection_prefix):]
                if exclude_current.startswith(collection_prefix)
                else exclude_current
            )
        with self._connect() as conn:
            row = conn.execute(
                """SELECT version_id FROM controlplane.dataset_versions
                   WHERE dataset=%s AND status='promoted' AND version_id != %s
                   ORDER BY updated_at DESC LIMIT 1""",
                (dataset, exclude_version),
            ).fetchone()
        return row[0] if row else None

    # ----------------------------------------------------------- quality gates
    def record_quality_report(self, verdict: GateVerdict) -> None:
        with self._connect() as conn:
            for check in verdict.checks:
                conn.execute(
                    """INSERT INTO controlplane.quality_reports
                       (version_id, check_name, passed, score, threshold, details)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        verdict.version_id,
                        check.check_name,
                        check.passed,
                        check.score,
                        check.threshold,
                        json.dumps(check.details, default=str),
                    ),
                )

    def get_quality_reports(self, version_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT check_name, passed, score, threshold, details, created_at
                   FROM controlplane.quality_reports WHERE version_id=%s
                   ORDER BY created_at""",
                (version_id,),
            ).fetchall()
        keys = ["check_name", "passed", "score", "threshold", "details", "created_at"]
        return [
            {k: (str(v) if k == "created_at" else v) for k, v in zip(keys, row, strict=False)}
            for row in rows
        ]

    # -------------------------------------------------------------- promotions
    def record_promotion(
        self,
        version_id: str,
        decision: str,
        from_target: str | None,
        to_target: str | None,
        reason: str,
        gate_summary: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO controlplane.promotions
                   (version_id, decision, from_target, to_target, reason, gate_summary)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    version_id,
                    decision,
                    from_target,
                    to_target,
                    reason,
                    json.dumps(gate_summary, default=str),
                ),
            )

    def list_promotions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT version_id, decision, from_target, to_target, reason, decided_at
                   FROM controlplane.promotions ORDER BY decided_at DESC LIMIT %s""",
                (limit,),
            ).fetchall()
        keys = ["version_id", "decision", "from_target", "to_target", "reason", "decided_at"]
        return [
            {k: (str(v) if k == "decided_at" else v) for k, v in zip(keys, row, strict=False)}
            for row in rows
        ]

    # -------------------------------------------------------------- quarantine
    def quarantine_records(
        self, version_id: str, quarantined: list[dict[str, Any]], key_field: str = "id"
    ) -> None:
        with self._connect() as conn:
            for item in quarantined:
                record = item.get("record", {})
                conn.execute(
                    """INSERT INTO controlplane.quarantine
                       (version_id, record_key, reason, payload)
                       VALUES (%s, %s, %s, %s)""",
                    (
                        version_id,
                        str(record.get(key_field, "")) if isinstance(record, dict) else "",
                        item.get("reason", "unknown"),
                        json.dumps(record, default=str),
                    ),
                )
