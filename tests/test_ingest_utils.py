"""Tests for shared ingestion helpers: parsing + input sanitisation."""

from __future__ import annotations

import pytest

from controlplane.ingest_utils import (
    IngestError,
    parse_records,
    sanitize_filename,
    validate_identifier,
)


class TestParseRecords:
    def test_json_array(self):
        assert parse_records('[{"id": 1}, {"id": 2}]') == [{"id": 1}, {"id": 2}]

    def test_single_line_object(self):
        assert parse_records('{"id": 1}') == [{"id": 1}]

    def test_multiline_pretty_printed_object(self):
        # This is the bug #15 case: a single pretty-printed JSON object that
        # spans multiple lines used to fall into the JSONL branch and fail.
        text = '{\n  "id": 1,\n  "title": "widget"\n}'
        assert parse_records(text) == [{"id": 1, "title": "widget"}]

    def test_multiline_pretty_printed_array(self):
        text = '[\n  {"id": 1},\n  {"id": 2}\n]'
        assert parse_records(text) == [{"id": 1}, {"id": 2}]

    def test_jsonl_fallback(self):
        text = '{"id": 1}\n{"id": 2}\n{"id": 3}'
        assert parse_records(text) == [{"id": 1}, {"id": 2}, {"id": 3}]

    def test_empty_raises(self):
        with pytest.raises(IngestError):
            parse_records("   ")

    def test_invalid_jsonl_line_raises_with_lineno(self):
        with pytest.raises(IngestError) as exc:
            parse_records('{"id": 1}\n{bad}')
        assert "line 2" in str(exc.value)

    def test_scalar_top_level_rejected(self):
        with pytest.raises(IngestError):
            parse_records("42")


class TestValidateIdentifier:
    @pytest.mark.parametrize("value", ["products", "documents", "my_dataset-1", "a"])
    def test_valid(self, value):
        assert validate_identifier(value) == value

    @pytest.mark.parametrize(
        "value",
        ["../../evil", "Products", "has space", "toolong" * 20, "", "a/b", "a;b"],
    )
    def test_invalid(self, value):
        with pytest.raises(IngestError):
            validate_identifier(value)


class TestSanitizeFilename:
    def test_strips_path_traversal(self):
        assert sanitize_filename("../../evil.json") == "evil.json"

    def test_strips_directories(self):
        assert sanitize_filename("/etc/passwd") == "passwd"

    def test_replaces_unsafe_chars(self):
        assert sanitize_filename("a b?c.json") == "a_b_c.json"

    def test_empty_becomes_upload(self):
        assert sanitize_filename("...") == "upload"
