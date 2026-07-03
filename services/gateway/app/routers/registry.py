"""Registry endpoints — lineage, quality reports, promotions, rollback."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from controlplane.promotion import PromotionEngine
from controlplane.stores import MetadataRegistry, QdrantStore

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/versions")
def list_versions(dataset: str | None = None, limit: int = 50) -> dict[str, Any]:
    registry = MetadataRegistry()
    return {"versions": registry.list_versions(dataset=dataset, limit=min(limit, 200))}


@router.get("/versions/{version_id}")
def version_detail(version_id: str) -> dict[str, Any]:
    """Full lineage of a dataset version: status + quality checks + promotions."""
    registry = MetadataRegistry()
    version = registry.get_version(version_id)
    if not version:
        raise HTTPException(404, f"unknown version '{version_id}'")
    return {
        "version": version,
        "quality_checks": registry.get_quality_reports(version_id),
    }


@router.get("/promotions")
def list_promotions(limit: int = 50) -> dict[str, Any]:
    registry = MetadataRegistry()
    return {"promotions": registry.list_promotions(limit=min(limit, 200))}


@router.post("/rollback/{dataset}")
def rollback(dataset: str, reason: str = "manual rollback via API") -> dict[str, Any]:
    """One-click rollback: re-point the prod alias to the previous promoted version."""
    engine = PromotionEngine(vector_store=QdrantStore(), registry=MetadataRegistry())
    result = engine.rollback(dataset, reason=reason)
    if result.get("decision") == "rollback_failed":
        raise HTTPException(409, result.get("reason", "rollback failed"))
    return result
