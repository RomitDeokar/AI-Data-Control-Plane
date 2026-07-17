"""Redis Streams event bus with idempotency, durable relay, and a dead-letter queue.

Why an event bus in front of Kestra?
------------------------------------
The gateway publishes ``dataset.ingested`` events here. This decouples ingestion
from orchestration: if Kestra is restarting when an upload arrives, the event is
still durably recorded on the stream and a background **relay** re-triggers the
pipeline later. That is what makes the platform's central reliability claim —
*"an accepted upload is never silently lost"* — actually true rather than a
comment that hopes a non-existent consumer will save the day.

Delivery semantics (honest version)
-----------------------------------
* **At the front door** — a duplicate upload (same content hash) is dropped by an
  ``SET NX`` idempotency lock, so we get effective *exactly-once processing* for
  identical payloads.
* **On the wire** — the stream + consumer group + delivery counter give
  *at-least-once* delivery. Every event carries a ``dispatched`` flag:

  ============  ================================================================
  scenario      what happens
  ============  ================================================================
  happy path    gateway triggers Kestra, immediately marks ``dispatched=true``
  Kestra down   trigger fails → event stays ``dispatched=false`` on the stream
  relay tick    relay claims undispatched events, re-triggers, marks dispatched
  relay crash   un-ACKed events are redelivered on the next relay read
  poison event  after ``event_max_deliveries`` tries it moves to the ``:dlq``
  ============  ================================================================
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

from controlplane.config import settings

logger = logging.getLogger(__name__)

DLQ_SUFFIX = ":dlq"
# Sentinel returned by publish() when an identical event was already accepted
# inside the idempotency window. Named (not a bare string literal scattered
# across call sites) so callers can compare against EventBus.DUPLICATE.
DUPLICATE = "duplicate"
# Per-event dispatch markers live under this prefix, one key per event id, each
# with its own TTL. (The old design used a single SET with one shared EXPIRE,
# so a single expire wiped *all* markers at once and the relay re-fired every
# recent event. Per-key TTLs make each marker expire independently.)
DISPATCH_KEY_PREFIX = "cp:dispatched:"


class EventBus:
    #: Sentinel returned by :meth:`publish` for a duplicate event.
    DUPLICATE = DUPLICATE

    def __init__(self, url: str | None = None, stream: str | None = None):
        self.redis = redis.Redis.from_url(url or settings.redis_url, decode_responses=True)
        self.stream = stream or settings.event_stream
        self.group = settings.event_consumer_group
        self.max_deliveries = settings.event_max_deliveries

    # ---------------------------------------------------------------- publish
    def publish(self, event_type: str, payload: dict[str, Any]) -> str:
        """Publish an event durably; idempotent per (event_type, idempotency_key).

        Returns the stream message id, or the string ``"duplicate"`` if an
        identical event was already accepted inside the idempotency window.
        """
        idempotency_key = payload.get("idempotency_key")
        if idempotency_key:
            lock_key = f"cp:idem:{event_type}:{idempotency_key}"
            # NX set = first writer wins; duplicates are silently dropped.
            if not self.redis.set(
                lock_key, "1", nx=True, ex=settings.idempotency_ttl_seconds
            ):
                logger.warning("duplicate event dropped: %s/%s", event_type, idempotency_key)
                return DUPLICATE

        event_id = self.redis.xadd(
            self.stream,
            {
                "type": event_type,
                "payload": json.dumps(payload, default=str),
                "dispatched": "0",
            },
        )
        logger.info("published %s → %s (%s)", event_type, self.stream, event_id)
        return event_id.decode() if isinstance(event_id, bytes) else str(event_id)

    def mark_dispatched(self, event_id: str) -> None:
        """Record that an event was successfully handed to Kestra.

        Each event gets its OWN key with its OWN TTL (a side marker rather than
        rewriting the immutable stream entry). Because markers expire
        individually, one expiry can never wipe the whole dispatch history and
        cause the relay to re-fire every recent event.
        """
        self.redis.set(
            f"{DISPATCH_KEY_PREFIX}{event_id}",
            "1",
            ex=settings.dispatch_ttl_seconds,
        )

    def is_dispatched(self, event_id: str) -> bool:
        return bool(self.redis.exists(f"{DISPATCH_KEY_PREFIX}{event_id}"))

    def _dispatched_count(self) -> int:
        """Best-effort count of live dispatch markers (for pending_stats)."""
        # SCAN keeps this O(N) without blocking Redis like KEYS would.
        return sum(1 for _ in self.redis.scan_iter(match=f"{DISPATCH_KEY_PREFIX}*"))

    # ---------------------------------------------------------------- consume
    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def consume(self, consumer: str, count: int = 10, block_ms: int = 2000) -> list[dict[str, Any]]:
        """Read *new* events for this consumer group (``>``)."""
        self.ensure_group()
        entries = self.redis.xreadgroup(
            self.group, consumer, {self.stream: ">"}, count=count, block=block_ms
        )
        return self._parse(entries)

    def claim_undispatched(self, consumer: str, count: int | None = None) -> list[dict[str, Any]]:
        """Return events that still need dispatching to Kestra.

        Reads both *new* messages and *previously-delivered-but-un-ACKed* messages
        (``0`` history) for this group, filters out any already marked dispatched,
        and attaches the per-message delivery count so the caller can dead-letter
        poison events. This is the relay's workhorse.
        """
        self.ensure_group()
        limit = count or settings.relay_batch_size

        # First drain our own pending backlog (id="0"): messages this consumer
        # was already delivered but never ACKed (e.g. a previous relay crashed
        # mid-flight, or Kestra was down last tick). Only if the backlog is empty
        # do we pull brand-new messages (id=">"). Reading the two ranges in
        # separate ticks — rather than both at once — is what keeps the delivery
        # counter honest (a fresh message must not count as delivered twice).
        backlog = self._parse(
            self.redis.xreadgroup(self.group, consumer, {self.stream: "0"}, count=limit)
        )
        source = backlog
        if not backlog:
            source = self._parse(
                self.redis.xreadgroup(
                    self.group, consumer, {self.stream: ">"}, count=limit, block=100
                )
            )

        events: list[dict[str, Any]] = []
        for event in source:
            mid = event["id"]
            if self.is_dispatched(mid):
                # Already handed to Kestra by the gateway; just ACK & skip.
                self.ack(mid)
                continue
            event["deliveries"] = self._delivery_count(mid)
            events.append(event)
        return events

    def _delivery_count(self, message_id: str) -> int:
        info = self.redis.xpending_range(
            self.stream, self.group, min=message_id, max=message_id, count=1
        )
        return int(info[0]["times_delivered"]) if info else 1

    @staticmethod
    def _parse(entries: Any) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for _stream, messages in entries or []:
            for message_id, fields in messages:
                events.append(
                    {
                        "id": message_id,
                        "type": fields.get("type"),
                        "payload": json.loads(fields.get("payload", "{}")),
                        "dispatched": fields.get("dispatched", "0") == "1",
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
            "dispatched": self._dispatched_count(),
        }
