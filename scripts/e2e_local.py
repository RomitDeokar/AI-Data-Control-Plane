#!/usr/bin/env python3
"""End-to-end pipeline simulation — no Docker required.

Runs the entire control-plane lifecycle in-process with in-memory fakes:

    ingest → validate → enrich → embed → quality gates → promote / reject → rollback

Use this to understand the platform in 10 seconds, or as a smoke test in CI:

    python scripts/e2e_local.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# repo root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from controlplane.embeddings import HashingEmbedder  # noqa: E402
from controlplane.enrichment import Enricher  # noqa: E402
from controlplane.models import new_version_id  # noqa: E402
from controlplane.promotion import PromotionEngine  # noqa: E402
from controlplane.quality import QualityGateRunner  # noqa: E402
from controlplane.validation import SchemaValidator, detect_drift  # noqa: E402
from tests.conftest import FakeRegistry, FakeVectorStore  # noqa: E402

SCHEMA = {
    "required": ["id", "title", "category", "price"],
    "types": {"id": "str", "title": "str", "category": "str", "price": "number"},
    "constraints": {"price": {"min": 0}, "title": {"min_length": 2}},
}

DATA_DIR = Path(__file__).parent.parent / "sample_data"


def banner(text: str) -> None:
    print(f"\n{'=' * 70}\n  {text}\n{'=' * 70}")


def run_pipeline(
    dataset: str,
    records: list[dict],
    engine: PromotionEngine,
    registry: FakeRegistry,
    previous_schema_hash: str | None,
) -> tuple[str, str | None]:
    """Run every stage; return (decision, schema_hash)."""
    version_id = new_version_id(dataset)
    print(f"→ version: {version_id}  ({len(records)} raw records)")

    # 1. validate
    result = SchemaValidator(SCHEMA).validate_batch(records)
    drift = detect_drift(previous_schema_hash, result.schema_hash)
    print(f"→ validate: {len(result.valid_records)} ok, "
          f"{len(result.quarantined)} quarantined "
          f"(pass rate {result.pass_rate:.1%}, drift={drift['drifted']})")

    # 2. enrich
    enriched, report = Enricher().enrich_batch(result.valid_records, version_id)
    print(f"→ enrich: {report['output_records']} records "
          f"({report['duplicates_removed']} duplicates removed)")

    # 3. embed
    embedded, emb_report = HashingEmbedder(dim=64).embed_batch(enriched)
    print(f"→ embed: coverage {emb_report['coverage']:.1%} "
          f"(dim={emb_report['dim']}, model={emb_report['model']})")

    # 4. quality gates
    verdict = QualityGateRunner(min_records=5).run_all(
        version_id=version_id,
        records=embedded,
        required_fields=SCHEMA["required"],
        validation_pass_rate=result.pass_rate,
        embedding_coverage=emb_report["coverage"],
        drift_report=drift,
    )
    for check in verdict.checks:
        icon = "✅" if check.passed else "❌"
        print(f"   {icon} {check.check_name:<24} score={check.score:.3f} "
              f"threshold={check.threshold}")

    # 5. promote / reject
    registry.register_version(
        type("V", (), {"dataset": dataset, "version_id": version_id, "status": "gated"})()
    )
    if verdict.passed:
        engine.stage(dataset, version_id, embedded, dim=64)
    decision = engine.decide(dataset, version_id, verdict)
    print(f"→ decision: {json.dumps(decision)}")
    return decision["decision"], result.schema_hash


def main() -> int:
    vector_store = FakeVectorStore()
    registry = FakeRegistry()
    engine = PromotionEngine(vector_store, registry)

    good = json.loads((DATA_DIR / "products_good.json").read_text())
    bad = json.loads((DATA_DIR / "products_bad.json").read_text())

    banner("RUN 1 — clean dataset (expect: PROMOTED)")
    decision1, schema_hash = run_pipeline("products", good, engine, registry, None)

    banner("RUN 2 — corrupted dataset (expect: REJECTED, prod unaffected)")
    decision2, _ = run_pipeline("products", bad, engine, registry, schema_hash)
    serving = vector_store.get_alias_target("products__prod")
    print(f"→ prod alias still serving: {serving}")

    banner("RUN 3 — clean dataset again (expect: PROMOTED, alias switches)")
    decision3, _ = run_pipeline("products", good, engine, registry, schema_hash)

    banner("ROLLBACK — instant blue/green revert")
    rollback = engine.rollback("products", reason="e2e demo rollback")
    print(f"→ {json.dumps(rollback)}")

    banner("SUMMARY")
    print(f"run 1: {decision1}   run 2: {decision2}   run 3: {decision3}")
    print(f"promotion ledger entries: {len(registry.promotions)}")
    ok = (decision1, decision2, decision3) == ("promoted", "rejected", "promoted") \
        and rollback["decision"] == "rolled_back"
    print("E2E RESULT:", "✅ PASS" if ok else "❌ FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
