"""Event relay — the safety net that makes "an accepted upload is never lost" TRUE.

Run on a schedule by ``flows/_system/event-relay.yaml`` (every minute). It drains
the Redis Streams event bus for ``dataset.ingested`` events that were **not**
dispatched to Kestra (``dispatched=false`` — e.g. Kestra was restarting when the
gateway received the upload), re-triggers the ``dataset-pipeline`` webhook for
each, ACKs successes, and reaps poison messages into the dead-letter queue after
``event_max_deliveries`` attempts.

Design notes
------------
* The gateway already triggers the pipeline on the happy path and marks the
  event dispatched, so the relay is a pure *backstop* — under normal operation
  it does nothing but ACK already-dispatched messages.
* Re-triggering is safe because the pipeline is idempotent per version and the
  gateway's ``SET NX`` idempotency lock already dropped duplicate *content*. A
  relayed re-trigger simply produces a fresh (validated, gated) version of the
  same source object; blue/green promotion guarantees production is untouched
  unless the new version passes every gate.

Usage::

    python -m controlplane.relay            # one drain pass (used by the flow)
    python -m controlplane.relay --loop     # continuous local daemon mode
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import time
from typing import Any

import httpx

from controlplane.config import settings
from controlplane.events import EventBus

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("controlplane.relay")

WEBHOOK_PATH = (
    "/api/v1/executions/webhook/controlplane.ingestion/"
    "dataset-pipeline/controlplane-webhook-key"
)


def _trigger_kestra(client: httpx.Client, payload: dict[str, Any]) -> bool:
    """Re-fire the dataset-pipeline webhook. Returns True on 2xx."""
    body = {
        "dataset": payload.get("dataset"),
        "source_uri": payload.get("source_uri"),
        "trigger_type": "relay",
    }
    resp = client.post(f"{settings.kestra_url}{WEBHOOK_PATH}", json=body, timeout=10.0)
    return resp.status_code in (200, 201)


def drain_once(bus: EventBus | None = None, consumer: str | None = None) -> dict[str, Any]:
    """Process one batch of stranded events. Returns a machine-readable report."""
    bus = bus or EventBus()
    consumer = consumer or f"relay-{socket.gethostname()}"
    redispatched = 0
    dead_lettered = 0
    skipped = 0

    events = bus.claim_undispatched(consumer)
    if not events:
        report = {"stage": "relay", "scanned": 0, "redispatched": 0, "dead_lettered": 0}
        logger.info("relay tick: nothing to do")
        return report

    with httpx.Client() as client:
        for event in events:
            mid = event["id"]
            deliveries = event.get("deliveries", 1)

            # Poison-message reaping: give up after N delivery attempts.
            if deliveries > bus.max_deliveries:
                bus.dead_letter(
                    mid, event, f"exceeded {bus.max_deliveries} delivery attempts"
                )
                dead_lettered += 1
                continue

            try:
                if _trigger_kestra(client, event["payload"]):
                    bus.mark_dispatched(mid)
                    bus.ack(mid)
                    redispatched += 1
                    logger.info("relayed %s (attempt %d)", mid, deliveries)
                else:
                    # leave un-ACKed; delivery count increments next tick
                    skipped += 1
                    logger.warning("kestra rejected relay of %s; will retry", mid)
            except httpx.HTTPError as exc:
                skipped += 1
                logger.warning("kestra unreachable while relaying %s: %s", mid, exc)

    report = {
        "stage": "relay",
        "scanned": len(events),
        "redispatched": redispatched,
        "dead_lettered": dead_lettered,
        "retry_pending": skipped,
    }
    print("::" + json.dumps({"outputs": report}) + "::")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="controlplane.relay")
    parser.add_argument("--loop", action="store_true", help="run continuously")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between loops")
    args = parser.parse_args(argv)

    bus = EventBus()
    if not args.loop:
        drain_once(bus)
        return 0

    logger.info("relay daemon starting (interval=%.1fs)", args.interval)
    while True:
        try:
            drain_once(bus)
        except Exception:  # noqa: BLE001 — daemon must not die on a transient error
            logger.exception("relay tick failed; continuing")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
