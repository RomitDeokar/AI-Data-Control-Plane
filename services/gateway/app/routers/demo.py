"""Interactive demo API — powers the Control Plane Console with zero infra.

These endpoints drive the live, in-browser demo using :mod:`controlplane.demo`,
which runs the REAL pipeline logic in memory. They let a portfolio visitor
ingest data, watch the pipeline stages execute, see quality-gate scorecards,
run semantic search against the promoted version, and roll back — all without a
running Kestra/Postgres/Qdrant stack.

In a full Docker deployment the same actions flow through the real gateway
routers (ingest → event bus → Kestra → Qdrant); this router is the
infrastructure-free twin for demos.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from controlplane.demo import DEMO_SCHEMAS, demo_engine

router = APIRouter(prefix="/demo", tags=["demo"])

_SAMPLE_DIR = Path(__file__).parent.parent.parent.parent.parent / "sample_data"


class RunRequest(BaseModel):
    dataset: str = Field(default="products")
    records: list[dict[str, Any]] = Field(default_factory=list)
    scenario: str | None = Field(
        default=None,
        description="Named built-in scenario: clean | corrupted | documents | drift",
    )
    trigger_type: str = Field(default="manual")


def _load_sample(name: str) -> list[dict[str, Any]]:
    path = _SAMPLE_DIR / name
    if not path.exists():
        return []
    text = path.read_text()
    if text.lstrip().startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# Built-in scenarios so the UI has one-click demos that always tell a story.
_SCENARIOS: dict[str, dict[str, Any]] = {
    "clean": {"dataset": "products", "file": "products_good.json",
              "label": "Clean products → should PROMOTE"},
    "corrupted": {"dataset": "products", "file": "products_bad.json",
                  "label": "Corrupted products → should REJECT at the gates"},
    "documents": {"dataset": "documents", "file": "documents.json",
                  "label": "Documents dataset → should PROMOTE"},
}


@router.get("/scenarios")
def scenarios() -> dict[str, Any]:
    """List the one-click demo scenarios and dataset schemas."""
    return {
        "scenarios": [
            {"id": k, "dataset": v["dataset"], "label": v["label"]}
            for k, v in _SCENARIOS.items()
        ],
        "schemas": DEMO_SCHEMAS,
    }


@router.post("/run")
def run(req: RunRequest) -> dict[str, Any]:
    """Run the full in-memory pipeline and return a stage-by-stage trace."""
    if req.scenario:
        spec = _SCENARIOS.get(req.scenario)
        if not spec:
            raise HTTPException(404, f"unknown scenario '{req.scenario}'")
        dataset = spec["dataset"]
        records = _load_sample(spec["file"])
        trigger = "event"
    else:
        dataset = req.dataset
        records = req.records
        trigger = req.trigger_type
        if not records:
            raise HTTPException(400, "provide 'records' or a 'scenario'")

    if len(records) > 5000:
        raise HTTPException(413, "demo is limited to 5000 records")

    return demo_engine.run_pipeline(dataset, records, trigger_type=trigger)


@router.get("/stats")
def stats() -> dict[str, Any]:
    return demo_engine.stats()


@router.get("/versions")
def versions(limit: int = 100) -> dict[str, Any]:
    return {"versions": demo_engine.versions(limit=limit)}


@router.get("/versions/{version_id}")
def version_detail(version_id: str) -> dict[str, Any]:
    detail = demo_engine.version_detail(version_id)
    if not detail:
        raise HTTPException(404, f"unknown version '{version_id}'")
    return detail


@router.get("/promotions")
def promotions(limit: int = 100) -> dict[str, Any]:
    return {"promotions": demo_engine.promotions(limit=limit)}


@router.get("/search/{dataset}")
def search(dataset: str, q: str, limit: int = 5) -> dict[str, Any]:
    if not q.strip():
        raise HTTPException(400, "query 'q' must not be empty")
    return demo_engine.search(dataset, q, limit=min(limit, 25))


@router.post("/rollback/{dataset}")
def rollback(dataset: str, reason: str = "console rollback") -> dict[str, Any]:
    result = demo_engine.rollback(dataset, reason=reason)
    if result.get("decision") == "rollback_failed":
        raise HTTPException(409, result.get("reason", "rollback failed"))
    return result


@router.post("/reset")
def reset() -> dict[str, Any]:
    demo_engine.reset()
    return {"status": "reset"}
