"""Tests for the quality gate suite — the promotion decision engine."""

from controlplane.quality import QualityGateRunner


def make_runner() -> QualityGateRunner:
    return QualityGateRunner(
        completeness_min=0.95, uniqueness_min=0.99,
        embedding_coverage_min=0.98, min_records=5,
    )


class TestQualityGates:
    def test_clean_data_passes_all_gates(self, good_records):
        verdict = make_runner().run_all(
            version_id="v1",
            records=good_records,
            required_fields=["id", "title", "category", "price"],
            validation_pass_rate=1.0,
            embedding_coverage=1.0,
            drift_report={"drifted": False},
        )
        assert verdict.passed
        assert all(c.passed for c in verdict.checks)

    def test_too_few_records_fails(self):
        verdict = make_runner().run_all(
            version_id="v1",
            records=[{"id": "1", "title": "x"}],
            required_fields=["id"],
        )
        assert not verdict.passed
        assert "min_records" in [c.check_name for c in verdict.checks if not c.passed]

    def test_incomplete_data_fails_completeness(self, good_records):
        # blank half the titles
        records = [dict(r) for r in good_records]
        for r in records[:10]:
            r["title"] = None
        verdict = make_runner().run_all(
            version_id="v1", records=records,
            required_fields=["id", "title", "category", "price"],
        )
        failed = [c.check_name for c in verdict.checks if not c.passed]
        assert "completeness" in failed
        assert not verdict.passed

    def test_duplicate_keys_fail_uniqueness(self, good_records):
        records = good_records + [good_records[0]] * 3
        verdict = make_runner().run_all(
            version_id="v1", records=records, required_fields=["id"],
        )
        failed = [c.check_name for c in verdict.checks if not c.passed]
        assert "uniqueness" in failed

    def test_low_embedding_coverage_fails(self, good_records):
        verdict = make_runner().run_all(
            version_id="v1", records=good_records,
            required_fields=["id"], embedding_coverage=0.5,
        )
        failed = [c.check_name for c in verdict.checks if not c.passed]
        assert "embedding_coverage" in failed

    def test_schema_drift_fails_gate(self, good_records):
        verdict = make_runner().run_all(
            version_id="v1", records=good_records,
            required_fields=["id"],
            drift_report={"drifted": True, "reason": "schema fingerprint changed"},
        )
        failed = [c.check_name for c in verdict.checks if not c.passed]
        assert "schema_drift" in failed

    def test_verdict_summary_lists_failed_checks(self, good_records):
        verdict = make_runner().run_all(
            version_id="v1", records=good_records,
            required_fields=["id"], embedding_coverage=0.1,
        )
        assert verdict.summary["failed_checks"] == ["embedding_coverage"]
        assert verdict.summary["version_id"] == "v1"

    def test_records_missing_key_drag_down_uniqueness(self):
        # Regression: previously records with no id were dropped from the ratio,
        # so a batch that is half missing-id could still report 100% unique.
        records = [{"id": "A"}, {"id": "B"}, {}, {}]  # 2 of 4 have no key
        runner = QualityGateRunner(min_records=1, uniqueness_min=0.99)
        verdict = runner.run_all(version_id="v1", records=records, required_fields=["id"])
        uniq = next(c for c in verdict.checks if c.check_name == "uniqueness")
        assert uniq.score == 0.5  # 2 unique keys / 4 records
        assert not uniq.passed
        assert uniq.details["records_missing_key"] == 2

    def test_validation_pass_rate_uses_its_own_threshold(self):
        # Regression: this gate used to reuse gate_completeness_min. Now it has a
        # dedicated knob, so a low completeness bar can't mask a bad pass rate.
        runner = QualityGateRunner(
            completeness_min=0.5, min_records=1, validation_pass_rate_min=0.95
        )
        verdict = runner.run_all(
            version_id="v1",
            records=[{"id": "A"}],
            required_fields=["id"],
            validation_pass_rate=0.80,  # below 0.95 → must fail
        )
        pr = next(c for c in verdict.checks if c.check_name == "validation_pass_rate")
        assert pr.threshold == 0.95
        assert not pr.passed
