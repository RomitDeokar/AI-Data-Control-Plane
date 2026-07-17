"""Tests for per-event dispatch markers with individual TTLs (#9)."""

from __future__ import annotations

import pytest

fakeredis = pytest.importorskip("fakeredis")

from controlplane.events import DISPATCH_KEY_PREFIX, EventBus  # noqa: E402


@pytest.fixture
def bus():
    fake = fakeredis.FakeRedis(decode_responses=True)
    b = EventBus.__new__(EventBus)
    b.redis = fake
    b.stream = "cp:events"
    b.group = "controlplane"
    b.max_deliveries = 3
    return b


class TestPerEventDispatchMarkers:
    def test_each_event_gets_its_own_key(self, bus):
        bus.mark_dispatched("1-0")
        bus.mark_dispatched("2-0")
        assert bus.is_dispatched("1-0")
        assert bus.is_dispatched("2-0")
        assert bus.redis.exists(f"{DISPATCH_KEY_PREFIX}1-0")
        assert bus.redis.exists(f"{DISPATCH_KEY_PREFIX}2-0")

    def test_expiring_one_marker_does_not_wipe_others(self, bus):
        """The old single-SET design lost ALL markers on one expire.

        With per-key TTLs, deleting/expiring one marker must leave the rest
        intact so the relay doesn't re-fire every recent event.
        """
        bus.mark_dispatched("1-0")
        bus.mark_dispatched("2-0")
        # Simulate marker 1-0 expiring.
        bus.redis.delete(f"{DISPATCH_KEY_PREFIX}1-0")
        assert not bus.is_dispatched("1-0")
        assert bus.is_dispatched("2-0")  # survives

    def test_marker_has_a_ttl(self, bus):
        bus.mark_dispatched("1-0")
        ttl = bus.redis.ttl(f"{DISPATCH_KEY_PREFIX}1-0")
        assert ttl > 0

    def test_dispatched_count_reflects_live_markers(self, bus):
        bus.mark_dispatched("1-0")
        bus.mark_dispatched("2-0")
        assert bus.pending_stats()["dispatched"] == 2
