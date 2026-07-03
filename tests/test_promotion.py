"""Tests for the blue/green promotion engine + rollback."""

from controlplane.models import GateVerdict, QualityCheckResult
from controlplane.promotion import PromotionEngine


def passing_verdict(version_id: str) -> GateVerdict:
    return GateVerdict(
        version_id=version_id, passed=True,
        checks=[QualityCheckResult("completeness", True, 1.0, 0.95)],
    )


def failing_verdict(version_id: str) -> GateVerdict:
    return GateVerdict(
        version_id=version_id, passed=False,
        checks=[QualityCheckResult("completeness", False, 0.4, 0.95)],
    )


def embedded_records(n: int = 5):
    return [
        {"id": f"P{i}", "title": f"Product {i}", "_vector": [0.1] * 8}
        for i in range(n)
    ]


class TestPromotion:
    def test_first_promotion_sets_alias(self, fake_vector_store, fake_registry):
        engine = PromotionEngine(fake_vector_store, fake_registry)
        engine.stage("products", "v1", embedded_records(), dim=8)
        decision = engine.decide("products", "v1", passing_verdict("v1"))

        assert decision["decision"] == "promoted"
        assert fake_vector_store.aliases["products__prod"] == "products__v1"
        assert decision["rollback_available"] is False

    def test_second_promotion_switches_alias(self, fake_vector_store, fake_registry):
        engine = PromotionEngine(fake_vector_store, fake_registry)
        for version in ("v1", "v2"):
            fake_registry.register_version(
                type("V", (), {"dataset": "products", "version_id": version, "status": "ingested"})()
            )
            engine.stage("products", version, embedded_records(), dim=8)
            engine.decide("products", version, passing_verdict(version))

        assert fake_vector_store.aliases["products__prod"] == "products__v2"
        # v1 collection retained for rollback
        assert "products__v1" in fake_vector_store.collections

    def test_failed_gates_reject_and_keep_serving_old(self, fake_vector_store, fake_registry):
        engine = PromotionEngine(fake_vector_store, fake_registry)
        engine.stage("products", "v1", embedded_records(), dim=8)
        engine.decide("products", "v1", passing_verdict("v1"))

        decision = engine.decide("products", "v2", failing_verdict("v2"))
        assert decision["decision"] == "rejected"
        assert decision["still_serving"] == "products__v1"
        assert fake_vector_store.aliases["products__prod"] == "products__v1"
        assert decision["failed_gates"] == ["completeness"]

    def test_rejection_recorded_in_ledger(self, fake_vector_store, fake_registry):
        engine = PromotionEngine(fake_vector_store, fake_registry)
        engine.decide("products", "v1", failing_verdict("v1"))
        assert fake_registry.promotions[-1]["decision"] == "rejected"
        assert "completeness" in fake_registry.promotions[-1]["reason"]

    def test_rollback_repoints_alias(self, fake_vector_store, fake_registry):
        engine = PromotionEngine(fake_vector_store, fake_registry)
        for version in ("v1", "v2"):
            fake_registry.register_version(
                type("V", (), {"dataset": "products", "version_id": version, "status": "ingested"})()
            )
            engine.stage("products", version, embedded_records(), dim=8)
            engine.decide("products", version, passing_verdict(version))

        assert fake_vector_store.aliases["products__prod"] == "products__v2"
        result = engine.rollback("products", reason="bad data detected")
        assert result["decision"] == "rolled_back"
        assert fake_vector_store.aliases["products__prod"] == "products__v1"

    def test_rollback_without_history_fails_gracefully(self, fake_vector_store, fake_registry):
        engine = PromotionEngine(fake_vector_store, fake_registry)
        result = engine.rollback("products")
        assert result["decision"] == "rollback_failed"

    def test_promotion_audit_contains_gate_summary(self, fake_vector_store, fake_registry):
        engine = PromotionEngine(fake_vector_store, fake_registry)
        engine.stage("products", "v1", embedded_records(), dim=8)
        engine.decide("products", "v1", passing_verdict("v1"))
        summary = fake_registry.promotions[-1]["gate_summary"]
        assert summary["passed"] is True
        assert summary["version_id"] == "v1"
