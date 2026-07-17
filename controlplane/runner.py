"""Pipeline runner — the single entry point Kestra tasks call.

Each stage is exposed as a CLI subcommand so Kestra YAML flows stay thin and all
business logic lives (tested) in Python:

    python -m controlplane.runner ingest    --dataset products --source-uri s3://raw/...
    python -m controlplane.runner validate  --version-id <id>
    python -m controlplane.runner enrich    --version-id <id>
    python -m controlplane.runner embed     --version-id <id>
    python -m controlplane.runner gate      --version-id <id>
    python -m controlplane.runner promote   --version-id <id>
    python -m controlplane.runner rollback  --dataset products

Stages communicate through the object store (staged/ zone) and the metadata
registry, so each stage is independently retryable — a Kestra retry re-reads
its inputs from storage instead of relying on in-memory state.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from controlplane.config import settings
from controlplane.embeddings import HashingEmbedder
from controlplane.enrichment import Enricher
from controlplane.ingest_utils import parse_records
from controlplane.models import DatasetVersion, TriggerType, VersionStatus, new_version_id
from controlplane.promotion import PromotionEngine
from controlplane.quality import QualityGateRunner
from controlplane.schemas import DATASET_SCHEMAS, schema_for
from controlplane.stores import MetadataRegistry, ObjectStore, QdrantStore
from controlplane.validation import SchemaValidator, detect_drift

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("controlplane.runner")

# Dataset contracts now live in controlplane.schemas (single source of truth).
# Re-exported under the historical name for backwards compatibility.
DEFAULT_SCHEMAS: dict[str, dict[str, Any]] = DATASET_SCHEMAS


def _emit(payload: dict[str, Any]) -> None:
    """Emit outputs in Kestra's native capture format.

    Kestra script tasks parse ``::{"outputs": {...}}::`` lines and expose the
    values as ``{{ outputs.<task_id>.vars.<key> }}`` in downstream tasks.
    """
    print("::" + json.dumps({"outputs": payload}, default=str) + "::")


# ============================================================== STAGE: INGEST
def cmd_ingest(args: argparse.Namespace) -> dict[str, Any]:
    store = ObjectStore()
    registry = MetadataRegistry()

    if args.source_uri:
        bucket, _, key = args.source_uri.replace("s3://", "").partition("/")
        raw = store.get_bytes(bucket, key)
        source_uri = args.source_uri
    elif args.local_file:
        with open(args.local_file, "rb") as fh:
            raw = fh.read()
        source_uri = store.write_raw(args.dataset, args.local_file.split("/")[-1], raw, "application/json")
    else:
        raise SystemExit("provide --source-uri or --local-file")

    records = parse_records(raw.decode())

    version_id = args.version_id or new_version_id(args.dataset)
    registry.register_version(
        DatasetVersion(
            dataset=args.dataset,
            version_id=version_id,
            source_uri=source_uri,
            pipeline=args.pipeline,
            trigger_type=args.trigger_type,
            record_count=len(records),
        )
    )
    store.write_staged(version_id, records)
    return {
        "stage": "ingest",
        "version_id": version_id,
        "dataset": args.dataset,
        "record_count": len(records),
        "source_uri": source_uri,
    }


# ============================================================ STAGE: VALIDATE
def cmd_validate(args: argparse.Namespace) -> dict[str, Any]:
    store = ObjectStore()
    registry = MetadataRegistry()
    version = registry.get_version(args.version_id)
    if not version:
        raise SystemExit(f"unknown version {args.version_id}")

    records = store.get_jsonl(settings.bucket_staged, f"{args.version_id}/records.jsonl")
    schema = schema_for(version["dataset"])

    result = SchemaValidator(schema).validate_batch(records)

    # drift vs last promoted version
    previous_hash = registry.get_last_promoted_schema_hash(version["dataset"])
    drift = detect_drift(previous_hash, result.schema_hash)

    # persist valid records forward, quarantine the rest
    store.write_staged(args.version_id, result.valid_records)
    if result.quarantined:
        store.write_quarantine(args.version_id, result.quarantined)
        registry.quarantine_records(args.version_id, result.quarantined)

    registry.update_version_status(
        args.version_id, VersionStatus.VALIDATED.value, record_count=len(result.valid_records)
    )
    report = {
        "stage": "validate",
        "version_id": args.version_id,
        "valid_records": len(result.valid_records),
        "quarantined": len(result.quarantined),
        "pass_rate": round(result.pass_rate, 4),
        "schema_hash": result.schema_hash,
        "drift": drift,
    }
    store.write_artifact(args.version_id, "validation_report", report)
    return report


# ============================================================== STAGE: ENRICH
def cmd_enrich(args: argparse.Namespace) -> dict[str, Any]:
    store = ObjectStore()
    registry = MetadataRegistry()
    records = store.get_jsonl(settings.bucket_staged, f"{args.version_id}/records.jsonl")

    enriched, report = Enricher().enrich_batch(records, args.version_id)
    store.write_staged(args.version_id, enriched)
    registry.update_version_status(
        args.version_id, VersionStatus.ENRICHED.value, record_count=len(enriched)
    )
    output = {"stage": "enrich", "version_id": args.version_id, **report}
    store.write_artifact(args.version_id, "enrichment_report", output)
    return output


# =============================================================== STAGE: EMBED
def cmd_embed(args: argparse.Namespace) -> dict[str, Any]:
    store = ObjectStore()
    registry = MetadataRegistry()
    records = store.get_jsonl(settings.bucket_staged, f"{args.version_id}/records.jsonl")

    embedder = HashingEmbedder(dim=settings.embedding_dim, model_name=settings.embedding_model)
    embedded, report = embedder.embed_batch(records)
    store.write_staged(args.version_id, embedded)
    registry.update_version_status(args.version_id, VersionStatus.EMBEDDED.value)

    output = {"stage": "embed", "version_id": args.version_id, **report}
    store.write_artifact(args.version_id, "embedding_report", output)
    return output


# ================================================================ STAGE: GATE
def cmd_gate(args: argparse.Namespace) -> dict[str, Any]:
    store = ObjectStore()
    registry = MetadataRegistry()
    version = registry.get_version(args.version_id)
    if version is None:
        raise ValueError(f"unknown version_id: {args.version_id}")
    records = store.get_jsonl(settings.bucket_staged, f"{args.version_id}/records.jsonl")

    validation = store.get_json(settings.bucket_artifacts, f"{args.version_id}/validation_report.json")
    embedding = store.get_json(settings.bucket_artifacts, f"{args.version_id}/embedding_report.json")
    schema = schema_for(version["dataset"])

    verdict = QualityGateRunner().run_all(
        version_id=args.version_id,
        records=records,
        required_fields=schema.get("required", []),
        validation_pass_rate=validation.get("pass_rate", 1.0),
        embedding_coverage=embedding.get("coverage"),
        drift_report=validation.get("drift"),
    )
    registry.record_quality_report(verdict)
    registry.update_version_status(args.version_id, VersionStatus.GATED.value)
    store.write_artifact(args.version_id, "quality_report", verdict.summary)

    return {"stage": "gate", **verdict.summary}


# ============================================================= STAGE: PROMOTE
def cmd_promote(args: argparse.Namespace) -> dict[str, Any]:
    store = ObjectStore()
    registry = MetadataRegistry()
    vectors = QdrantStore()
    version = registry.get_version(args.version_id)
    if version is None:
        raise ValueError(f"unknown version_id: {args.version_id}")
    records = store.get_jsonl(settings.bucket_staged, f"{args.version_id}/records.jsonl")
    quality = store.get_json(settings.bucket_artifacts, f"{args.version_id}/quality_report.json")

    from controlplane.models import GateVerdict, QualityCheckResult

    verdict = GateVerdict(
        version_id=args.version_id,
        passed=quality["passed"],
        checks=[
            QualityCheckResult(
                check_name=c["check"], passed=c["passed"],
                score=c["score"], threshold=c["threshold"], details=c.get("details", {}),
            )
            for c in quality["checks"]
        ],
    )

    engine = PromotionEngine(vector_store=vectors, registry=registry)
    if verdict.passed:
        engine.stage(version["dataset"], args.version_id, records, dim=settings.embedding_dim)
    decision = engine.decide(version["dataset"], args.version_id, verdict)

    store.write_artifact(args.version_id, "promotion_decision", decision)
    return {"stage": "promote", "version_id": args.version_id, **decision}


# ============================================================ STAGE: ROLLBACK
def cmd_rollback(args: argparse.Namespace) -> dict[str, Any]:
    engine = PromotionEngine(vector_store=QdrantStore(), registry=MetadataRegistry())
    return {"stage": "rollback", **engine.rollback(args.dataset, reason=args.reason)}


# ==================================================================== CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="controlplane.runner")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest")
    p.add_argument("--dataset", required=True)
    p.add_argument("--source-uri")
    p.add_argument("--local-file")
    p.add_argument("--version-id")
    p.add_argument("--pipeline", default="generic")
    p.add_argument("--trigger-type", default=TriggerType.MANUAL.value)
    p.set_defaults(func=cmd_ingest)

    for name, func in [
        ("validate", cmd_validate),
        ("enrich", cmd_enrich),
        ("embed", cmd_embed),
        ("gate", cmd_gate),
        ("promote", cmd_promote),
    ]:
        p = sub.add_parser(name)
        p.add_argument("--version-id", required=True)
        p.set_defaults(func=func)

    p = sub.add_parser("rollback")
    p.add_argument("--dataset", required=True)
    p.add_argument("--reason", default="manual rollback")
    p.set_defaults(func=cmd_rollback)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except Exception:
        logger.exception("stage failed")
        return 1
    _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
