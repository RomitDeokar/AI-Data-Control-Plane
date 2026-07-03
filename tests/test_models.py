"""Tests for domain models: version ids, schema fingerprints."""

from controlplane.models import new_version_id, schema_fingerprint


class TestVersionId:
    def test_contains_dataset_name(self):
        assert new_version_id("products").startswith("products-")

    def test_unique(self):
        ids = {new_version_id("x") for _ in range(50)}
        assert len(ids) == 50

    def test_sortable_by_time(self):
        first = new_version_id("d")
        second = new_version_id("d")
        # timestamp prefix means lexicographic order == chronological order
        assert first.rsplit("-", 1)[0] <= second.rsplit("-", 1)[0]


class TestSchemaFingerprint:
    def test_stable_for_same_shape(self):
        a = [{"id": "1", "title": "x", "price": 1.0}]
        b = [{"id": "2", "title": "y", "price": 9.9}]
        assert schema_fingerprint(a) == schema_fingerprint(b)

    def test_changes_when_field_added(self):
        a = [{"id": "1", "title": "x"}]
        b = [{"id": "1", "title": "x", "color": "red"}]
        assert schema_fingerprint(a) != schema_fingerprint(b)

    def test_changes_when_type_changes(self):
        a = [{"id": "1", "price": 1.0}]
        b = [{"id": "1", "price": "1.0"}]
        assert schema_fingerprint(a) != schema_fingerprint(b)

    def test_field_order_irrelevant(self):
        a = [{"a": 1, "b": "x"}]
        b = [{"b": "y", "a": 2}]
        assert schema_fingerprint(a) == schema_fingerprint(b)
