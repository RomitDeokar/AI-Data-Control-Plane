"""Ingestion endpoints: file upload + webhook → raw zone → event bus → Kestra."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from prometheus_client import Counter
from pydantic import BaseModel, Field

from controlplane.config import settings
from controlplane.events import EventBus
from controlplane.stores import ObjectStore

logger = logging.getLogger(__name__)
router = APIRouter()

INGEST_EVENTS = Counter(
    "gateway_ingest_events_total", "Ingestion events published", ["dataset", "source"]
)


class WebhookPayload(BaseModel):
    dataset: str = Field(..., examples=["products"], min_length=1, max_length=64)
    records: list[dict[str, Any]] = Field(..., min_length=1)
    pipeline: str = Field(default="generic")


def _trigger_kestra_flow(dataset: str, source_uri: str, trigger_type: str) -> dict[str, Any]:
    """Fire the Kestra ingestion flow via its execution API webhook."""
    try:
        response = httpx.post(
            f"{settings.kestra_url}/api/v1/executions/webhook/controlplane.ingestion/"
            f"dataset-pipeline/controlplane-webhook-key",
            json={"dataset": dataset, "source_uri": source_uri, "trigger_type": trigger_type},
            timeout=10.0,
        )
        if response.status_code in (200, 201):
            body = response.json()
            return {"triggered": True, "execution_id": body.get("id")}
        logger.warning("kestra trigger returned %s: %s", response.status_code, response.text[:300])
        return {"triggered": False, "status_code": response.status_code}
    except httpx.HTTPError as exc:
        # Not fatal — the event is on the bus; Kestra's polling trigger will pick it up.
        logger.warning("kestra unreachable (%s); event remains queued on the bus", exc)
        return {"triggered": False, "reason": "kestra unreachable, event queued"}


@router.post("/ingest/upload")
async def upload_file(
    dataset: str = Form(...),
    pipeline: str = Form(default="generic"),
    file: UploadFile = File(...),  # noqa: B008 — canonical FastAPI dependency pattern
) -> dict[str, Any]:
    """Upload a JSON/JSONL file. It lands in the raw zone and triggers the pipeline."""
    if not file.filename or not file.filename.endswith((".json", ".jsonl")):
        raise HTTPException(400, "only .json / .jsonl files are accepted")

    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "file exceeds 50MB limit")

    # validate it actually parses before accepting — bad payloads never reach
    # the raw zone or the event bus (fail fast at the front door).
    try:
        text = data.decode()
        stripped = text.lstrip()
        if stripped.startswith("["):
            # JSON array
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise HTTPException(400, "JSON array expected")
            record_count = len(parsed)
        elif stripped.startswith("{") and "\n" not in stripped.rstrip():
            # single JSON object on one line — parse to confirm it is valid
            json.loads(text)
            record_count = 1
        else:
            # JSONL: every non-empty line must be a valid JSON object
            lines = [line for line in text.splitlines() if line.strip()]
            for line in lines:
                json.loads(line)
            record_count = len(lines)
        if record_count == 0:
            raise HTTPException(400, "payload contains no records")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"invalid JSON payload: {exc}") from exc

    store = ObjectStore()
    content_hash = hashlib.sha256(data).hexdigest()[:12]
    key = f"{content_hash}-{file.filename}"
    source_uri = store.write_raw(dataset, key, data, "application/json")

    bus = EventBus()
    event_id = bus.publish(
        "dataset.ingested",
        {
            "dataset": dataset,
            "source_uri": source_uri,
            "pipeline": pipeline,
            "record_count": record_count,
            "trigger_type": "event",
            "idempotency_key": content_hash,
        },
    )
    INGEST_EVENTS.labels(dataset=dataset, source="upload").inc()

    if event_id == "duplicate":
        return {
            "status": "duplicate",
            "detail": "identical file already ingested (idempotency check)",
            "source_uri": source_uri,
        }

    kestra = _trigger_kestra_flow(dataset, source_uri, "event")
    return {
        "status": "accepted",
        "dataset": dataset,
        "source_uri": source_uri,
        "record_count": record_count,
        "event_id": event_id,
        "kestra": kestra,
    }


@router.post("/ingest/webhook")
async def webhook_ingest(payload: WebhookPayload) -> dict[str, Any]:
    """Push records directly via JSON webhook (e.g. from an upstream system)."""
    store = ObjectStore()
    body = json.dumps(payload.records, default=str).encode()
    content_hash = hashlib.sha256(body).hexdigest()[:12]
    source_uri = store.write_raw(
        payload.dataset, f"webhook-{content_hash}.json", body, "application/json"
    )

    bus = EventBus()
    event_id = bus.publish(
        "dataset.ingested",
        {
            "dataset": payload.dataset,
            "source_uri": source_uri,
            "pipeline": payload.pipeline,
            "record_count": len(payload.records),
            "trigger_type": "webhook",
            "idempotency_key": content_hash,
        },
    )
    INGEST_EVENTS.labels(dataset=payload.dataset, source="webhook").inc()

    if event_id == "duplicate":
        return {"status": "duplicate", "source_uri": source_uri}

    kestra = _trigger_kestra_flow(payload.dataset, source_uri, "webhook")
    return {
        "status": "accepted",
        "dataset": payload.dataset,
        "source_uri": source_uri,
        "record_count": len(payload.records),
        "event_id": event_id,
        "kestra": kestra,
    }
