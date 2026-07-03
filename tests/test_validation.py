"""Tests for schema validation, quarantining, and drift detection."""

from controlplane.validation import SchemaValidator, detect_drift


class TestSchemaValidator:
    def test_all_valid_records_pass(self, product_schema, good_records):
        result = SchemaValidator(product_schema).validate_batch(good_records)
        assert len(result.valid_records) == 20
        assert len(result.quarantined) == 0
        assert result.pass_rate == 1.0

    def test_missing_required_field_quarantined(self, product_schema):
        records = [{"id": "P1", "category": "home", "price": 5.0}]  # no title
        result = SchemaValidator(product_schema).validate_batch(records)
        assert len(result.quarantined) == 1
        assert "title" in result.quarantined[0]["reason"]

    def test_empty_string_counts_as_missing(self, product_schema):
        records = [{"id": "P1", "title": "", "category": "home", "price": 5.0}]
        result = SchemaValidator(product_schema).validate_batch(records)
        assert len(result.quarantined) == 1

    def test_wrong_type_quarantined(self, product_schema):
        records = [{"id": "P1", "title": "Thing", "category": "home", "price": "free"}]
        result = SchemaValidator(product_schema).validate_batch(records)
        assert len(result.quarantined) == 1
        assert "expected number" in result.quarantined[0]["reason"]

    def test_constraint_min_violated(self, product_schema):
        records = [{"id": "P1", "title": "Thing", "category": "home", "price": -1}]
        result = SchemaValidator(product_schema).validate_batch(records)
        assert len(result.quarantined) == 1
        assert "below min" in result.quarantined[0]["reason"]

    def test_min_length_violated(self, product_schema):
        records = [{"id": "P1", "title": "X", "category": "home", "price": 1.0}]
        result = SchemaValidator(product_schema).validate_batch(records)
        assert len(result.quarantined) == 1

    def test_mixed_batch_partial_quarantine(self, product_schema, good_records):
        bad = [{"id": "B1", "category": "misc", "price": 1.0}]
        result = SchemaValidator(product_schema).validate_batch(good_records + bad)
        assert len(result.valid_records) == 20
        assert len(result.quarantined) == 1
        assert 0.9 < result.pass_rate < 1.0

    def test_int_accepted_for_number(self, product_schema):
        records = [{"id": "P1", "title": "Thing", "category": "home", "price": 5}]
        result = SchemaValidator(product_schema).validate_batch(records)
        assert len(result.valid_records) == 1


class TestDriftDetection:
    def test_first_ingest_never_drifts(self):
        report = detect_drift(None, "abc123")
        assert report["drifted"] is False

    def test_same_hash_no_drift(self):
        report = detect_drift("abc123", "abc123")
        assert report["drifted"] is False

    def test_changed_hash_drifts(self):
        report = detect_drift("abc123", "def456")
        assert report["drifted"] is True
