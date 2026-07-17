"""Postgres metadata registry — the audit trail of the control plane.

Every version, quality report, quarantined record, and promotion decision is
persisted here, making the whole system auditable and queryable.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import psycopg

try:  # optional dependency — pooling is a performance optimisation, not required
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover - exercised only when the extra is absent
    ConnectionPool = None  # type: ignore[assignment,misc]

from controlplane.config import settings
from controlplane.models import DatasetVersion, GateVerdict

logger = logging.getLogger(__name__)

# One shared pool per database URL, created lazily. Opening a fresh TCP + auth
# handshake for every single registry call (the old behaviour) is wasteful; a
# small pool amortises that across the many short-lived operations the pipeline
# performs. Falls back to direct connections if psycopg_pool isn't installed.
_POOLS: dict[str, Any] = {}
_POOLS_LOCK = threading.Lock()


def _get_pool(database_url: str) -> Any | None:
    if ConnectionPool is None:
        return None
    with _POOLS_LOCK:
        pool = _POOLS.get(database_url)
        if pool is None:
            # A short connect_timeout makes health checks fail FAST when
            # Postgres is unreachable (demo mode) instead of blocking on the
            # pool's reconnect loop. num_workers/timeout keep checkouts snappy.
            pool = ConnectionPool(
                database_url,
                min_size=0,
                max_size=int(settings.db_pool_max_size),
                kwargs={
                    "autocommit": True,
                    "connect_timeout": int(settings.db_connect_timeout),
                },
                timeout=float(settings.db_connect_timeout),
                open=True,
                check=None,
            )
            _POOLS[database_url] = pool
        return pool


class MetadataRegistry:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or settings.database_url

    def _connect(self) -> psycopg.Connection:
        """Yield a connection, from the shared pool when available.

        The returned context manager restores the connection to the pool on
        exit (pool mode) or closes it (direct mode) — callers just use
        ``with self._connect() as conn:`` in both cases.
        """
        pool = _get_pool(self.database_url)
        if pool is not None:
            return pool.connection()
        return psycopg.connect(self.database_url, autocommit=True)

    def ping(self) -> bool:
        """Public liveness probe: open a connection, run ``SELECT 1``, close.

        Lets health checks verify Postgres without reaching into the private
        ``_connect`` helper.
        """
        with self._connect() as conn:
            conn.execute("SELECT 1")
        return True

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

    def _normalise_version(self, dataset: str, value: str | None) -> str:
        """Strip a ``{dataset}__`` collection prefix down to a bare version id."""
        prefix = f"{dataset}__"
        if value and value.startswith(prefix):
            return value[len(prefix):]
        return value or ""

    def get_promotion_history(self, dataset: str, limit: int = 50) -> list[str]:
        """Version ids promoted for ``dataset``, newest first, by LEDGER order.

        Ordering is by the promotion ledger's monotonic ``id`` sequence — NOT by
        the mutation timestamp ``updated_at``. This is what makes rollback
        deterministic: a rollback rewrites ``updated_at`` on the rolled-to
        version, so ordering by ``updated_at`` would let two rollbacks bounce
        between the same two versions. The append-only ledger never changes for
        past events, so it gives a stable "what did we promote, in what order"
        answer. Duplicates (a version promoted more than once) are de-duplicated,
        keeping the most recent ledger position.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT p.version_id
                   FROM controlplane.promotions p
                   JOIN controlplane.dataset_versions v
                     ON v.version_id = p.version_id
                   WHERE v.dataset = %s AND p.decision = 'promoted'
                   ORDER BY p.id DESC
                   LIMIT %s""",
                (dataset, limit),
            ).fetchall()
        seen: set[str] = set()
        history: list[str] = []
        for (vid,) in rows:
            if vid not in seen:
                seen.add(vid)
                history.append(vid)
        return history

    def get_previous_promoted(self, dataset: str, exclude_current: str | None) -> str | None:
        """Version id of the previously-promoted version (by ledger order).

        ``exclude_current`` may be a bare ``version_id`` *or* a collection name
        (``{dataset}__{version_id}``) — the latter is what the vector store's
        ``get_alias_target`` returns. Returns the most recent promoted version
        in the ledger that is not the current one.
        """
        exclude_version = self._normalise_version(dataset, exclude_current)
        for vid in self.get_promotion_history(dataset):
            if vid != exclude_version:
                return vid
        return None

    # ----------------------------------------------------------- quality gates
    def record_quality_report(self, verdict: GateVerdict) -> None:
        """Persist all gate checks for a version in ONE batched transaction.

        Previously this did one autocommit INSERT per check (a round-trip each,
        and a crash mid-loop left a partial audit trail). ``executemany`` inside
        a single transaction makes the whole report atomic and fast.
        """
        rows = [
            (
                verdict.version_id,
                check.check_name,
                check.passed,
                check.score,
                check.threshold,
                json.dumps(check.details, default=str),
            )
            for check in verdict.checks
        ]
        if not rows:
            return
        with self._connect() as conn, conn.transaction():
            conn.cursor().executemany(
                """INSERT INTO controlplane.quality_reports
                   (version_id, check_name, passed, score, threshold, details)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                rows,
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
        """Persist quarantined rows in ONE batched transaction.

        1000 quarantined rows used to be 1000 autocommit round-trips, with a
        crash leaving a partial audit trail. ``executemany`` inside a single
        transaction makes it atomic and dramatically faster — important for a
        system whose selling point is an auditable trail.
        """
        rows = []
        for item in quarantined:
            record = item.get("record", {})
            rows.append(
                (
                    version_id,
                    str(record.get(key_field, "")) if isinstance(record, dict) else "",
                    item.get("reason", "unknown"),
                    json.dumps(record, default=str),
                )
            )
        if not rows:
            return
        with self._connect() as conn, conn.transaction():
            conn.cursor().executemany(
                """INSERT INTO controlplane.quarantine
                   (version_id, record_key, reason, payload)
                   VALUES (%s, %s, %s, %s)""",
                rows,
            )
