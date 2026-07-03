"""Tests for the durable event relay — the backstop that guarantees an accepted
upload is eventually processed even if Kestra was unreachable at ingest time.

These use fakeredis (no real Redis) and a stubbed Kestra trigger, so they run in
CI with zero infrastructure while still exercising the real relay logic:
claiming undispatched events, re-triggering, ACKing, and dead-lettering poison
messages after the max delivery count.
"""

from __future__ import annotations

import pytest

fakeredis = pytest.importorskip("fakeredis")

from controlplane import relay as relay_module  # noqa: E402
from controlplane.events import EventBus  # noqa: E402


@pytest.fixture
def bus():
    fake = fakeredis.FakeRedis(decode_responses=True)
    b = EventBus.__new__(EventBus)
    b.redis = fake
    b.stream = "cp:events"
    b.group = "controlplane"
    b.max_deliveries = 3
    return b


def _ingest(bus, key="abc", dataset="products"):
    return bus.publish(
        "dataset.ingested",
        {"dataset": dataset, "source_uri": "s3://raw/x", "idempotency_key": key},
    )


class TestDispatchTracking:
    def test_new_event_is_not_dispatched(self, bus):
        _ingest(bus)
        events = bus.claim_undispatched("relay-1")
        assert len(events) == 1
        assert events[0]["deliveries"] == 1

    def test_dispatched_events_are_skipped_and_acked(self, bus):
        event_id = _ingest(bus)
        bus.mark_dispatched(event_id)  # simulate gateway happy path
        events = bus.claim_undispatched("relay-1")
        assert events == []  # nothing left for the relay to do
        assert bus.pending_stats()["pending"] == 0


class TestRelayRedelivery:
    def test_relay_retriggers_undispatched_event(self, bus, monkeypatch):
        _ingest(bus)
        monkeypatch.setattr(relay_module, "_trigger_kestra", lambda client, payload: True)

        report = relay_module.drain_once(bus, consumer="relay-1")

        assert report["redispatched"] == 1
        assert report["dead_lettered"] == 0
        assert bus.pending_stats()["pending"] == 0

    def test_relay_leaves_event_pending_when_kestra_down(self, bus, monkeypatch):
        _ingest(bus)
        monkeypatch.setattr(relay_module, "_trigger_kestra", lambda client, payload: False)

        report = relay_module.drain_once(bus, consumer="relay-1")

        assert report["redispatched"] == 0
        assert report["retry_pending"] == 1
        # event is un-ACKed → still pending for the next tick
        assert bus.pending_stats()["pending"] == 1

    def test_poison_event_is_dead_lettered_after_max_deliveries(self, bus, monkeypatch):
        _ingest(bus)
        monkeypatch.setattr(relay_module, "_trigger_kestra", lambda client, payload: False)

        # Ticks 1..max_deliveries keep retrying (delivery count 1..3 ≤ 3).
        # The next tick sees delivery count 4 > max_deliveries and reaps it.
        for _ in range(bus.max_deliveries):
            report = relay_module.drain_once(bus, consumer="relay-1")
            assert report["dead_lettered"] == 0
        final = relay_module.drain_once(bus, consumer="relay-1")

        assert final["dead_lettered"] == 1
        stats = bus.pending_stats()
        assert stats["dlq_length"] == 1
        assert stats["pending"] == 0

    def test_nothing_to_do_is_a_noop(self, bus, monkeypatch):
        monkeypatch.setattr(relay_module, "_trigger_kestra", lambda client, payload: True)
        report = relay_module.drain_once(bus, consumer="relay-1")
        assert report["scanned"] == 0
        assert report["redispatched"] == 0
