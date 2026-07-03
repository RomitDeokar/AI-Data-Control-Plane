"""Tests for the event bus using fakeredis (no real Redis needed in CI)."""

import pytest

fakeredis = pytest.importorskip("fakeredis")

from controlplane.events import EventBus  # noqa: E402


@pytest.fixture
def bus(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    event_bus = EventBus.__new__(EventBus)
    event_bus.redis = fake
    event_bus.stream = "cp:events"
    event_bus.group = "controlplane"
    return event_bus


class TestEventBus:
    def test_publish_and_consume(self, bus):
        bus.publish("dataset.ingested", {"dataset": "products", "source_uri": "s3://raw/x"})
        events = bus.consume("worker-1", block_ms=1)
        assert len(events) == 1
        assert events[0]["type"] == "dataset.ingested"
        assert events[0]["payload"]["dataset"] == "products"

    def test_idempotency_drops_duplicates(self, bus):
        payload = {"dataset": "products", "idempotency_key": "abc123"}
        first = bus.publish("dataset.ingested", payload)
        second = bus.publish("dataset.ingested", payload)
        assert first != "duplicate"
        assert second == "duplicate"
        assert bus.redis.xlen("cp:events") == 1

    def test_ack_removes_from_pending(self, bus):
        bus.publish("dataset.ingested", {"dataset": "d"})
        events = bus.consume("worker-1", block_ms=1)
        bus.ack(events[0]["id"])
        stats = bus.pending_stats()
        assert stats["pending"] == 0

    def test_dead_letter_queue(self, bus):
        bus.publish("dataset.ingested", {"dataset": "poison"})
        events = bus.consume("worker-1", block_ms=1)
        bus.dead_letter(events[0]["id"], events[0], "simulated processing error")
        stats = bus.pending_stats()
        assert stats["dlq_length"] == 1
        assert stats["pending"] == 0
