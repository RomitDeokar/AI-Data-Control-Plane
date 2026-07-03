"""Redis Streams event bus with idempotency + dead-letter queue.

Why an event bus in front of Kestra?
------------------------------------
The gateway publishes ``dataset.ingested`` events here. This decouples ingestion
from orchestration: if Kestra is restarting, events queue up instead of being
lost. Consumers use a consumer group so events are processed exactly once per
group, and poison messages are routed to a dead-letter stream after max retries.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

from controlplane.config import settings

logger = logging.getLogger(__name__)

DLQ_SUFFIX = ":dlq"
MAX_DELIVERIES = 3


class EventBus:
    def __init__(self, url: str | None = None, stream: str | None = None):
        self.redis = redis.Redis.from_url(url or settings.redis_url, decode_responses=True)
        self.stream = stream or settings.event_stream
        self.group = settings.event_consumer_group

    # ---------------------------------------------------------------- publish
    def publish(self, event_type: str, payload: dict[str, Any]) -> str:
        """Publish an event; idempotent per (event_type, idempotency_key)."""
        idempotency_key = payload.get("idempotency_key")
        if idempotency_key:
            lock_key = f"cp:idem:{event_type}:{idempotency_key}"
            # NX set = first writer wins; duplicates are silently dropped
            if not self.redis.set(lock_key, "1", nx=True, ex=86400):
                logger.warning("duplicate event dropped: %s/%s", event_type, idempotency_key)
                return "duplicate"

        event_id = self.redis.xadd(
            self.stream,
            {"type": event_type, "payload": json.dumps(payload, default=str)},
        )
        logger.info("published %s → %s (%s)", event_type, self.stream, event_id)
        return event_id

    # ---------------------------------------------------------------- consume
    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def consume(self, consumer: str, count: int = 10, block_ms: int = 2000) -> list[dict[str, Any]]:
        """Read new events for this consumer group."""
        self.ensure_group()
        entries = self.redis.xreadgroup(
            self.group, consumer, {self.stream: ">"}, count=count, block=block_ms
        )
        events: list[dict[str, Any]] = []
        for _stream, messages in entries or []:
            for message_id, fields in messages:
                events.append(
                    {
                        "id": message_id,
                        "type": fields.get("type"),
                        "payload": json.loads(fields.get("payload", "{}")),
                    }
                )
        return events

    def ack(self, message_id: str) -> None:
        self.redis.xack(self.stream, self.group, message_id)

    def dead_letter(self, message_id: str, event: dict[str, Any], error: str) -> None:
        """Move a poison message to the DLQ and ack it on the main stream."""
        self.redis.xadd(
            self.stream + DLQ_SUFFIX,
            {
                "original_id": message_id,
                "type": event.get("type", "unknown"),
                "payload": json.dumps(event.get("payload", {}), default=str),
                "error": error,
            },
        )
        self.ack(message_id)
        logger.error("dead-lettered %s: %s", message_id, error)

    def pending_stats(self) -> dict[str, Any]:
        self.ensure_group()
        info = self.redis.xpending(self.stream, self.group)
        return {
            "pending": info.get("pending", 0),
            "stream_length": self.redis.xlen(self.stream),
            "dlq_length": self.redis.xlen(self.stream + DLQ_SUFFIX),
        }
