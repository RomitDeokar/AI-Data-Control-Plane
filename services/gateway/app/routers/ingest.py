"""Ingestion endpoints: file upload + webhook → raw zone → event bus → Kestra."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx
from app.security import require_api_key
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from prometheus_client import Counter
from pydantic import BaseModel, Field, field_validator

from controlplane.config import settings
from controlplane.events import DUPLICATE as EVENT_DUPLICATE
from controlplane.events import EventBus
from controlplane.ingest_utils import (
    IngestError,
    parse_records,
    sanitize_filename,
    validate_identifier,
)
from controlplane.stores import ObjectStore

logger = logging.getLogger(__name__)
router = APIRouter()

# Read uploads in bounded chunks so a huge body is rejected before it is fully
# buffered into memory (a 5GB upload must not OOM the gateway).
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024

INGEST_EVENTS = Counter(
    "gateway_ingest_events_total", "Ingestion events published", ["dataset", "source"]
)


class WebhookPayload(BaseModel):
    dataset: str = Field(..., examples=["products"], min_length=1, max_length=64)
    records: list[dict[str, Any]] = Field(..., min_length=1)
    pipeline: str = Field(default="generic")

    @field_validator("dataset")
    @classmethod
    def _validate_dataset(cls, v: str) -> str:
        try:
            return validate_identifier(v, field="dataset")
        except IngestError as exc:
            raise ValueError(str(exc)) from exc


def _kestra_webhook_url() -> str:
    """Build the Kestra webhook URL from the single shared key in settings."""
    return (
        f"{settings.kestra_url}/api/v1/executions/webhook/controlplane.ingestion/"
        f"dataset-pipeline/{settings.webhook_key}"
    )


def _trigger_kestra_flow(dataset: str, source_uri: str, trigger_type: str) -> dict[str, Any]:
    """Fire the Kestra ingestion flow via its execution API webhook."""
    try:
        response = httpx.post(
            _kestra_webhook_url(),
            json={"dataset": dataset, "source_uri": source_uri, "trigger_type": trigger_type},
            timeout=10.0,
        )
        if response.status_code in (200, 201):
            body = response.json()
            return {"triggered": True, "execution_id": body.get("id")}
        logger.warning("kestra trigger returned %s: %s", response.status_code, response.text[:300])
        return {"triggered": False, "status_code": response.status_code}
    except httpx.HTTPError as exc:
        # Not fatal — the event is durably on the stream with dispatched=false.
        # The event-relay flow (controlplane.relay) re-triggers it once Kestra
        # is reachable again, so an accepted upload is never silently lost.
        logger.warning("kestra unreachable (%s); event queued for relay redelivery", exc)
        return {"triggered": False, "reason": "kestra unreachable, event queued for relay"}


@router.post("/ingest/upload", dependencies=[Depends(require_api_key)])
async def upload_file(
    dataset: str = Form(...),
    pipeline: str = Form(default="generic"),
    file: UploadFile = File(...),  # noqa: B008 — canonical FastAPI dependency pattern
) -> dict[str, Any]:
    """Upload a JSON/JSONL file. It lands in the raw zone and triggers the pipeline."""
    # Sanitise the dataset before it flows into bucket paths / collection names.
    try:
        dataset = validate_identifier(dataset, field="dataset")
    except IngestError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not file.filename or not file.filename.endswith((".json", ".jsonl")):
        raise HTTPException(400, "only .json / .jsonl files are accepted")

    # Stream the body in bounded chunks and abort the moment we exceed the
    # limit — a multi-GB upload can no longer OOM the gateway before the check.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(413, "file exceeds 50MB limit")
        chunks.append(chunk)
    data = b"".join(chunks)

    # validate it actually parses before accepting — bad payloads never reach
    # the raw zone or the event bus (fail fast at the front door). The shared
    # parser tries a whole-document parse first, so multi-line pretty-printed
    # JSON objects are accepted, then falls back to JSONL.
    try:
        records = parse_records(data.decode())
    except UnicodeDecodeError as exc:
        raise HTTPException(400, f"file is not valid UTF-8 text: {exc}") from exc
    except IngestError as exc:
        raise HTTPException(400, f"invalid JSON payload: {exc}") from exc
    record_count = len(records)

    store = ObjectStore()
    content_hash = hashlib.sha256(data).hexdigest()[:12]
    key = f"{content_hash}-{sanitize_filename(file.filename)}"
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

    if event_id == EVENT_DUPLICATE:
        return {
            "status": "duplicate",
            "detail": "identical file already ingested (idempotency check)",
            "source_uri": source_uri,
        }

    kestra = _trigger_kestra_flow(dataset, source_uri, "event")
    if kestra.get("triggered"):
        # Only now is the event safely in Kestra's hands — mark it dispatched so
        # the relay won't redundantly re-fire it. If this call had failed the
        # event stays dispatched=false and the relay picks it up later.
        bus.mark_dispatched(event_id)
    return {
        "status": "accepted",
        "dataset": dataset,
        "source_uri": source_uri,
        "record_count": record_count,
        "event_id": event_id,
        "kestra": kestra,
    }


@router.post("/ingest/webhook", dependencies=[Depends(require_api_key)])
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

    if event_id == EVENT_DUPLICATE:
        return {"status": "duplicate", "source_uri": source_uri}

    kestra = _trigger_kestra_flow(payload.dataset, source_uri, "webhook")
    if kestra.get("triggered"):
        bus.mark_dispatched(event_id)
    return {
        "status": "accepted",
        "dataset": payload.dataset,
        "source_uri": source_uri,
        "record_count": len(payload.records),
        "event_id": event_id,
        "kestra": kestra,
    }
