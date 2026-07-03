"""In-memory demo engine — runs the REAL control-plane lifecycle without Docker.

This powers the interactive Control Plane Console so the platform is fully
demonstrable in a live sandbox (or a portfolio link) with zero infrastructure.
It reuses the genuine business logic — :class:`SchemaValidator`,
:class:`Enricher`, :class:`HashingEmbedder`, :class:`QualityGateRunner`,
:class:`PromotionEngine` — and only swaps the *stores* for in-memory fakes.

Nothing here is mocked away: validation really quarantines bad rows, gates
really compute scores, promotion really switches a blue/green alias, and search
really runs cosine similarity over the promoted collection. It is the same code
path the Dockerized pipeline runs, minus the network hops.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from controlplane.config import settings
from controlplane.embeddings import HashingEmbedder
from controlplane.enrichment import Enricher
from controlplane.models import (
    DatasetVersion,
    GateVerdict,
    new_version_id,
)
from controlplane.promotion import PromotionEngine
from controlplane.quality import QualityGateRunner
from controlplane.validation import SchemaValidator, detect_drift

# Dataset contracts (mirror controlplane.runner.DEFAULT_SCHEMAS).
DEMO_SCHEMAS: dict[str, dict[str, Any]] = {
    "products": {
        "required": ["id", "title", "category", "price"],
        "types": {"id": "str", "title": "str", "category": "str", "price": "number"},
        "constraints": {"price": {"min": 0}, "title": {"min_length": 2}},
    },
    "documents": {
        "required": ["id", "title", "content"],
        "types": {"id": "str", "title": "str", "content": "str"},
        "constraints": {"content": {"min_length": 10}},
    },
}


# --------------------------------------------------------------------------- fakes
class _MemVectorStore:
    """In-memory vector store with cosine search and blue/green aliases."""

    def __init__(self) -> None:
        self.collections: dict[str, list[dict[str, Any]]] = {}
        self.aliases: dict[str, str] = {}

    def create_collection(self, name: str, dim: int) -> None:
        self.collections.setdefault(name, [])

    def drop_collection(self, name: str) -> None:
        self.collections.pop(name, None)

    def list_collections(self) -> list[str]:
        return list(self.collections)

    def upsert(self, collection: str, points: list[dict[str, Any]], batch_size: int = 256) -> int:
        self.collections.setdefault(collection, []).extend(points)
        return len(points)

    def set_alias(self, alias: str, collection: str) -> None:
        self.aliases[alias] = collection

    def get_alias_target(self, alias: str) -> str | None:
        return self.aliases.get(alias)

    def search(self, alias_or_collection: str, vector: list[float], limit: int = 5):
        collection = self.aliases.get(alias_or_collection, alias_or_collection)
        points = self.collections.get(collection, [])
        scored = []
        for point in points:
            score = _cosine(vector, point["vector"])
            scored.append({"score": score, "payload": point["payload"]})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]


class _MemRegistry:
    """In-memory registry capturing the audit trail the UI renders."""

    def __init__(self) -> None:
        self.versions: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self.promotions: list[dict[str, Any]] = []
        self.quality: dict[str, list[dict[str, Any]]] = {}
        self.quarantine: dict[str, list[dict[str, Any]]] = {}

    def register_version(self, version: DatasetVersion) -> None:
        self.versions[version.version_id] = {
            "dataset": version.dataset,
            "version_id": version.version_id,
            "source_uri": version.source_uri,
            "pipeline": version.pipeline,
            "trigger_type": version.trigger_type,
            "status": version.status,
            "record_count": version.record_count,
            "schema_hash": version.schema_hash,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.order.append(version.version_id)

    def update_version_status(self, version_id: str, status: str, record_count=None) -> None:
        row = self.versions.setdefault(version_id, {"version_id": version_id})
        row["status"] = status
        if record_count is not None:
            row["record_count"] = record_count

    def record_promotion(self, **kwargs) -> None:
        kwargs["decided_at"] = datetime.now(UTC).isoformat()
        self.promotions.append(kwargs)

    def record_quality_report(self, verdict: GateVerdict) -> None:
        self.quality[verdict.version_id] = [c.as_dict() for c in verdict.checks]

    def quarantine_records(self, version_id, items, key_field="id") -> None:
        self.quarantine[version_id] = items

    def get_last_promoted_schema_hash(self, dataset: str) -> str | None:
        for vid in reversed(self.order):
            v = self.versions[vid]
            if v["dataset"] == dataset and v["status"] == "promoted":
                return v.get("schema_hash")
        return None

    def get_previous_promoted(self, dataset: str, exclude_current: str | None) -> str | None:
        exclude = (exclude_current or "").replace(f"{dataset}__", "", 1)
        promoted = [
            v["version_id"]
            for vid in self.order
            if (v := self.versions[vid])["dataset"] == dataset
            and v["status"] == "promoted"
            and v["version_id"] != exclude
        ]
        return promoted[-1] if promoted else None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return round(dot / (na * nb), 4) if na and nb else 0.0


# --------------------------------------------------------------------------- engine
@dataclass
class StageEvent:
    stage: str
    status: str  # running | done | failed | skipped
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class DemoEngine:
    """Thread-safe, in-process control plane for the interactive console."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.vectors = _MemVectorStore()
        self.registry = _MemRegistry()
        self.embedder = HashingEmbedder(dim=settings.embedding_dim, model_name=settings.embedding_model)
        self.engine = PromotionEngine(vector_store=self.vectors, registry=self.registry)
        self.timeline: list[dict[str, Any]] = []

    # ----------------------------------------------------------------- pipeline
    def run_pipeline(
        self, dataset: str, records: list[dict[str, Any]], trigger_type: str = "manual"
    ) -> dict[str, Any]:
        """Run ingest → validate → enrich → embed → gate → promote/reject.

        Returns a full trace (per-stage events + final decision) that the UI
        animates as a live pipeline graph.
        """
        with self._lock:
            events: list[StageEvent] = []
            schema = DEMO_SCHEMAS.get(dataset, {"required": ["id"], "types": {}, "constraints": {}})
            version_id = new_version_id(dataset)

            # 1. INGEST
            self.registry.register_version(
                DatasetVersion(
                    dataset=dataset,
                    version_id=version_id,
                    source_uri=f"mem://{dataset}/{version_id}",
                    pipeline="demo-pipeline",
                    trigger_type=trigger_type,
                    record_count=len(records),
                )
            )
            events.append(
                StageEvent("ingest", "done", f"registered {len(records)} raw records",
                           {"raw_records": len(records), "version_id": version_id})
            )

            # 2. VALIDATE (+ drift)
            result = SchemaValidator(schema).validate_batch(records)
            previous_hash = self.registry.get_last_promoted_schema_hash(dataset)
            drift = detect_drift(previous_hash, result.schema_hash)
            if result.quarantined:
                self.registry.quarantine_records(version_id, result.quarantined)
            self.registry.update_version_status(version_id, "validated", len(result.valid_records))
            self.registry.versions[version_id]["schema_hash"] = result.schema_hash
            events.append(
                StageEvent(
                    "validate", "done",
                    f"{len(result.valid_records)} valid · {len(result.quarantined)} quarantined",
                    {
                        "valid": len(result.valid_records),
                        "quarantined": len(result.quarantined),
                        "pass_rate": round(result.pass_rate, 4),
                        "drift": drift["drifted"],
                        "quarantine_samples": [
                            {"reason": q["reason"], "id": q.get("record", {}).get("id")}
                            for q in result.quarantined[:6]
                        ],
                    },
                )
            )

            # 3. ENRICH
            enriched, enrich_report = Enricher().enrich_batch(result.valid_records, version_id)
            self.registry.update_version_status(version_id, "enriched", len(enriched))
            events.append(
                StageEvent("enrich", "done",
                           f"{len(enriched)} records · {enrich_report['duplicates_removed']} dupes removed",
                           enrich_report)
            )

            # 4. EMBED
            embedded, embed_report = self.embedder.embed_batch(enriched)
            self.registry.update_version_status(version_id, "embedded")
            events.append(
                StageEvent("embed", "done",
                           f"coverage {embed_report['coverage']*100:.0f}% · dim {embed_report['dim']}",
                           embed_report)
            )

            # 5. QUALITY GATES
            verdict = QualityGateRunner().run_all(
                version_id=version_id,
                records=embedded,
                required_fields=schema.get("required", []),
                validation_pass_rate=result.pass_rate,
                embedding_coverage=embed_report["coverage"],
                drift_report=drift,
            )
            self.registry.record_quality_report(verdict)
            self.registry.update_version_status(version_id, "gated")
            events.append(
                StageEvent(
                    "gate", "done" if verdict.passed else "failed",
                    f"{'PASS' if verdict.passed else 'FAIL'} · "
                    f"{sum(c.passed for c in verdict.checks)}/{len(verdict.checks)} checks",
                    {"passed": verdict.passed, "checks": [c.as_dict() for c in verdict.checks]},
                )
            )

            # 6. PROMOTE / REJECT
            if verdict.passed:
                self.engine.stage(dataset, version_id, embedded, dim=settings.embedding_dim)
            decision = self.engine.decide(dataset, version_id, verdict)
            events.append(
                StageEvent(
                    "promote", "done" if verdict.passed else "skipped",
                    decision["decision"].upper(),
                    decision,
                )
            )

            trace = {
                "version_id": version_id,
                "dataset": dataset,
                "trigger_type": trigger_type,
                "passed": verdict.passed,
                "decision": decision["decision"],
                "events": [e.__dict__ for e in events],
                "serving": self.vectors.get_alias_target(f"{dataset}__prod"),
            }
            self.timeline.append(
                {"version_id": version_id, "dataset": dataset, "decision": decision["decision"],
                 "ts": datetime.now(UTC).isoformat()}
            )
            return trace

    # ------------------------------------------------------------------- queries
    def search(self, dataset: str, query: str, limit: int = 5) -> dict[str, Any]:
        vector = self.embedder.embed_text(query)
        alias = f"{dataset}__prod"
        serving = self.vectors.get_alias_target(alias)
        if not serving:
            return {"dataset": dataset, "query": query, "serving_collection": None, "results": []}
        hits = self.vectors.search(alias, vector, limit=limit)
        return {
            "dataset": dataset,
            "query": query,
            "serving_collection": serving,
            "results": [
                {
                    "score": h["score"],
                    "payload": {k: v for k, v in h["payload"].items() if not k.startswith("_")},
                    "version_id": h["payload"].get("_meta", {}).get("version_id"),
                }
                for h in hits
            ],
        }

    def rollback(self, dataset: str, reason: str = "console rollback") -> dict[str, Any]:
        with self._lock:
            return self.engine.rollback(dataset, reason=reason)

    def versions(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = [self.registry.versions[v] for v in reversed(self.registry.order)]
        return rows[:limit]

    def promotions(self, limit: int = 200) -> list[dict[str, Any]]:
        return list(reversed(self.registry.promotions))[:limit]

    def version_detail(self, version_id: str) -> dict[str, Any] | None:
        version = self.registry.versions.get(version_id)
        if not version:
            return None
        return {
            "version": version,
            "quality_checks": self.registry.quality.get(version_id, []),
            "quarantine": self.registry.quarantine.get(version_id, []),
        }

    def stats(self) -> dict[str, Any]:
        promoted = sum(1 for p in self.registry.promotions if p.get("decision") == "promoted")
        rejected = sum(1 for p in self.registry.promotions if p.get("decision") == "rejected")
        rolled = sum(1 for p in self.registry.promotions if p.get("decision") == "rolled_back")
        datasets = {v["dataset"] for v in self.registry.versions.values()}
        serving = {
            ds: self.vectors.get_alias_target(f"{ds}__prod") for ds in datasets
        }
        return {
            "versions": len(self.registry.versions),
            "promoted": promoted,
            "rejected": rejected,
            "rolled_back": rolled,
            "datasets": len(datasets),
            "collections": len(self.vectors.collections),
            "serving": serving,
        }

    def reset(self) -> None:
        with self._lock:
            self.__init__()


# module-level singleton used by the gateway demo router
demo_engine = DemoEngine()
