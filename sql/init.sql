-- ============================================================================
-- AI Data Control Plane — Metadata Registry Schema
-- Tracks every dataset version, quality report, and promotion decision.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS controlplane;

-- Every ingestion event creates a dataset version (immutable, append-only)
CREATE TABLE IF NOT EXISTS controlplane.dataset_versions (
    id              BIGSERIAL PRIMARY KEY,
    dataset         TEXT        NOT NULL,               -- logical dataset name (e.g. "products", "documents")
    version_id      TEXT        NOT NULL UNIQUE,        -- e.g. "products-20260703-142530-a1b2"
    source_uri      TEXT        NOT NULL,               -- s3://raw/... origin object
    pipeline        TEXT        NOT NULL,               -- which pipeline template processed it
    trigger_type    TEXT        NOT NULL,               -- event | schedule | manual | webhook
    status          TEXT        NOT NULL DEFAULT 'ingested',
                    -- ingested → validated → enriched → embedded → gated → promoted | quarantined | rolled_back
    record_count    INTEGER,
    schema_hash     TEXT,                               -- fingerprint for drift detection
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quality gate results — one row per check per version
CREATE TABLE IF NOT EXISTS controlplane.quality_reports (
    id              BIGSERIAL PRIMARY KEY,
    version_id      TEXT        NOT NULL REFERENCES controlplane.dataset_versions(version_id),
    check_name      TEXT        NOT NULL,               -- completeness | uniqueness | schema_drift | embedding_coverage ...
    passed          BOOLEAN     NOT NULL,
    score           DOUBLE PRECISION,                   -- 0.0 → 1.0
    threshold       DOUBLE PRECISION,
    details         JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Promotion ledger — audit trail of every promote / reject / rollback decision
CREATE TABLE IF NOT EXISTS controlplane.promotions (
    id              BIGSERIAL PRIMARY KEY,
    version_id      TEXT        NOT NULL REFERENCES controlplane.dataset_versions(version_id),
    decision        TEXT        NOT NULL,               -- promoted | rejected | rolled_back
    from_target     TEXT,                               -- e.g. staging collection name
    to_target       TEXT,                               -- e.g. production collection/alias
    reason          TEXT,
    gate_summary    JSONB       NOT NULL DEFAULT '{}',
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quarantined records — failed rows routed here instead of killing the pipeline
CREATE TABLE IF NOT EXISTS controlplane.quarantine (
    id              BIGSERIAL PRIMARY KEY,
    version_id      TEXT        NOT NULL,
    record_key      TEXT,
    reason          TEXT        NOT NULL,
    payload         JSONB       NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Schema registry — expected schemas per dataset, versioned
CREATE TABLE IF NOT EXISTS controlplane.schema_registry (
    id              BIGSERIAL PRIMARY KEY,
    dataset         TEXT        NOT NULL,
    schema_version  INTEGER     NOT NULL DEFAULT 1,
    json_schema     JSONB       NOT NULL,
    is_active       BOOLEAN     NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset, schema_version)
);

CREATE INDEX IF NOT EXISTS idx_versions_dataset  ON controlplane.dataset_versions (dataset, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_versions_status   ON controlplane.dataset_versions (status);
CREATE INDEX IF NOT EXISTS idx_quality_version   ON controlplane.quality_reports (version_id);
CREATE INDEX IF NOT EXISTS idx_promotions_ver    ON controlplane.promotions (version_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_ver    ON controlplane.quarantine (version_id);
