"""Health checks + event-bus stats for observability."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from controlplane.events import EventBus
from controlplane.stores import MetadataRegistry, QdrantStore

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness + dependency checks. Degrades gracefully — reports each dep."""
    checks: dict[str, str] = {}

    try:
        EventBus().redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"

    try:
        MetadataRegistry()._connect().close()
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {exc}"

    try:
        QdrantStore().list_collections()
        checks["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["qdrant"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if healthy else "degraded", "checks": checks}


@router.get("/events/stats")
def event_stats() -> dict[str, Any]:
    """Event bus depth, pending messages, and dead-letter queue length."""
    try:
        return EventBus().pending_stats()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
