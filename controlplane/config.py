"""Central configuration — every setting overridable via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    # --- infrastructure endpoints -------------------------------------------
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", "postgresql://kestra:k3str4@localhost:5432/kestra"
        )
    )
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))
    minio_endpoint: str = field(default_factory=lambda: _env("MINIO_ENDPOINT", "localhost:9000"))
    minio_access_key: str = field(default_factory=lambda: _env("MINIO_ACCESS_KEY", "minioadmin"))
    minio_secret_key: str = field(default_factory=lambda: _env("MINIO_SECRET_KEY", "minioadmin"))
    qdrant_url: str = field(default_factory=lambda: _env("QDRANT_URL", "http://localhost:6333"))
    kestra_url: str = field(default_factory=lambda: _env("KESTRA_URL", "http://localhost:8080"))

    # --- buckets (data zones) ------------------------------------------------
    bucket_raw: str = "raw"
    bucket_staged: str = "staged"
    bucket_quarantine: str = "quarantine"
    bucket_artifacts: str = "artifacts"

    # --- event bus ------------------------------------------------------------
    event_stream: str = field(default_factory=lambda: _env("EVENT_STREAM", "cp:events"))
    event_consumer_group: str = "controlplane"
    # How long an idempotency key is remembered (seconds). A duplicate upload
    # within this window is dropped at the front door. 24h by default.
    idempotency_ttl_seconds: int = field(
        default_factory=lambda: int(_env("IDEMPOTENCY_TTL_SECONDS", "86400"))
    )
    # A relay claims a stranded event, then re-triggers Kestra. If delivery keeps
    # failing this many times the event is dead-lettered instead of looping forever.
    event_max_deliveries: int = field(
        default_factory=lambda: int(_env("EVENT_MAX_DELIVERIES", "3"))
    )
    # How many stranded events the relay drains per tick.
    relay_batch_size: int = field(default_factory=lambda: int(_env("RELAY_BATCH_SIZE", "50")))

    # --- embeddings ------------------------------------------------------------
    embedding_dim: int = field(default_factory=lambda: int(_env("EMBEDDING_DIM", "256")))
    embedding_model: str = field(
        default_factory=lambda: _env("EMBEDDING_MODEL", "hashing-tfidf-v1")
    )

    # --- quality gate thresholds ------------------------------------------------
    gate_completeness_min: float = field(
        default_factory=lambda: float(_env("GATE_COMPLETENESS_MIN", "0.95"))
    )
    gate_uniqueness_min: float = field(
        default_factory=lambda: float(_env("GATE_UNIQUENESS_MIN", "0.99"))
    )
    gate_embedding_coverage_min: float = field(
        default_factory=lambda: float(_env("GATE_EMBEDDING_COVERAGE_MIN", "0.98"))
    )
    gate_drift_max: float = field(default_factory=lambda: float(_env("GATE_DRIFT_MAX", "0.25")))
    gate_min_records: int = field(default_factory=lambda: int(_env("GATE_MIN_RECORDS", "1")))
    # Dedicated threshold for the "validation pass rate" gate. Previously this
    # gate reused gate_completeness_min, silently coupling two unrelated knobs.
    gate_validation_pass_rate_min: float = field(
        default_factory=lambda: float(_env("GATE_VALIDATION_PASS_RATE_MIN", "0.95"))
    )


settings = Settings()
