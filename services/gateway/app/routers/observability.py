"""Health checks + event-bus stats for observability."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from controlplane.events import EventBus
from controlplane.stores import MetadataRegistry, QdrantStore

logger = logging.getLogger(__name__)
router = APIRouter()


def _dependency_checks() -> dict[str, str]:
    """Probe each backing dependency; returns ``{dep: "ok" | "error: ..."}``."""
    checks: dict[str, str] = {}

    try:
        EventBus().redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"

    try:
        MetadataRegistry().ping()
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {exc}"

    try:
        QdrantStore().list_collections()
        checks["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["qdrant"] = f"error: {exc}"

    return checks


@router.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness + dependency checks. Degrades gracefully — reports each dep."""
    checks = _dependency_checks()
    healthy = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if healthy else "degraded", "checks": checks}


@router.get("/mode")
def mode() -> dict[str, Any]:
    """Report whether the gateway is running against real infra or demo-only.

    The console uses this to show an *honest* status indicator instead of a
    hardcoded green dot:

    * ``full``  — every backing dependency (redis, postgres, qdrant) is reachable.
    * ``degraded`` — some but not all dependencies are reachable.
    * ``demo``  — no backing infra is reachable; only the in-memory demo engine
      is serving. This is the expected state for a portfolio/sandbox deploy.
    """
    checks = _dependency_checks()
    ok = [k for k, v in checks.items() if v == "ok"]
    if len(ok) == len(checks):
        mode_value = "full"
    elif ok:
        mode_value = "degraded"
    else:
        mode_value = "demo"
    return {"mode": mode_value, "checks": checks, "dependencies_ok": ok}


@router.get("/events/stats")
def event_stats() -> dict[str, Any]:
    """Event bus depth, pending messages, and dead-letter queue length."""
    try:
        return EventBus().pending_stats()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
