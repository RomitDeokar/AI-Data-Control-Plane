"""Regression tests for rollback ordering (#6) and retention safety (#5)."""

from __future__ import annotations

from controlplane.models import GateVerdict, QualityCheckResult
from controlplane.promotion import PromotionEngine


def verdict(version_id: str) -> GateVerdict:
    return GateVerdict(
        version_id=version_id,
        passed=True,
        checks=[QualityCheckResult("completeness", True, 1.0, 0.95)],
    )


def records(n: int = 3):
    return [{"id": f"P{i}", "title": f"P{i}", "_vector": [0.1] * 8} for i in range(n)]


def _promote(engine, registry, version):
    registry.register_version(
        type("V", (), {"dataset": "products", "version_id": version, "status": "ingested"})()
    )
    engine.stage("products", version, records(), dim=8)
    engine.decide("products", version, verdict(version))


class TestLedgerOrdering:
    def test_rollback_does_not_bounce_between_two_versions(self, fake_vector_store, fake_registry):
        """After a rollback, a *second* rollback must not bounce back.

        With updated_at ordering, rolling back to v1 refreshed v1's timestamp,
        so the next rollback picked v1 again (or bounced). Ledger ordering keeps
        the history stable: v3 promoted last, rollback → v2, rollback → v1.
        """
        engine = PromotionEngine(fake_vector_store, fake_registry)
        for v in ("v1", "v2", "v3"):
            _promote(engine, fake_registry, v)
        assert fake_vector_store.aliases["products__prod"] == "products__v3"

        r1 = engine.rollback("products")
        assert r1["now_serving"] == "products__v2"

        r2 = engine.rollback("products")
        assert r2["now_serving"] == "products__v1"

    def test_promotion_history_is_ledger_ordered(self, fake_vector_store, fake_registry):
        engine = PromotionEngine(fake_vector_store, fake_registry)
        for v in ("v1", "v2", "v3"):
            _promote(engine, fake_registry, v)
        history = fake_registry.get_promotion_history("products")
        assert history == ["v3", "v2", "v1"]


class TestRetentionSafety:
    def test_rollback_skips_dropped_collection(self, fake_vector_store, fake_registry):
        """Rollback must never target a collection the retention window dropped."""
        engine = PromotionEngine(fake_vector_store, fake_registry)
        for v in ("v1", "v2", "v3"):
            _promote(engine, fake_registry, v)

        # Simulate retention having dropped v2's collection.
        fake_vector_store.drop_collection("products__v2")

        result = engine.rollback("products")
        # v2 is gone → must fall through to v1 (which still exists).
        assert result["decision"] == "rolled_back"
        assert result["now_serving"] == "products__v1"

    def test_rollback_fails_when_no_live_previous(self, fake_vector_store, fake_registry):
        engine = PromotionEngine(fake_vector_store, fake_registry)
        for v in ("v1", "v2"):
            _promote(engine, fake_registry, v)
        # Drop every prior collection.
        fake_vector_store.drop_collection("products__v1")
        result = engine.rollback("products")
        assert result["decision"] == "rollback_failed"
