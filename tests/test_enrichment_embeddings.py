"""Tests for enrichment (normalization, dedup) and the hashing embedder."""

import math

from controlplane.embeddings import HashingEmbedder
from controlplane.enrichment import Enricher


class TestEnricher:
    def test_normalizes_whitespace(self):
        records = [{"id": "1", "title": "  Hello   World  "}]
        enriched, _ = Enricher().enrich_batch(records, "v1")
        assert enriched[0]["title"] == "Hello World"

    def test_removes_duplicates_by_id(self):
        records = [
            {"id": "1", "title": "A"},
            {"id": "1", "title": "A copy"},
            {"id": "2", "title": "B"},
        ]
        enriched, report = Enricher().enrich_batch(records, "v1")
        assert len(enriched) == 2
        assert report["duplicates_removed"] == 1

    def test_builds_search_text(self):
        records = [{"id": "1", "title": "Widget", "description": "A great widget"}]
        enriched, _ = Enricher().enrich_batch(records, "v1")
        assert "Widget" in enriched[0]["_search_text"]
        assert "A great widget" in enriched[0]["_search_text"]

    def test_attaches_lineage_metadata(self):
        records = [{"id": "1", "title": "Widget"}]
        enriched, _ = Enricher().enrich_batch(records, "version-42")
        meta = enriched[0]["_meta"]
        assert meta["version_id"] == "version-42"
        assert len(meta["content_hash"]) == 16


class TestHashingEmbedder:
    def test_deterministic(self):
        embedder = HashingEmbedder(dim=64)
        assert embedder.embed_text("hello world") == embedder.embed_text("hello world")

    def test_l2_normalized(self):
        vector = HashingEmbedder(dim=128).embed_text("the quick brown fox")
        norm = math.sqrt(sum(v * v for v in vector))
        assert abs(norm - 1.0) < 1e-9

    def test_empty_text_gives_zero_vector(self):
        vector = HashingEmbedder(dim=32).embed_text("")
        assert all(v == 0.0 for v in vector)

    def test_similar_texts_more_similar_than_different(self):
        embedder = HashingEmbedder(dim=256)
        a = embedder.embed_text("wireless bluetooth headphones with noise cancelling")
        b = embedder.embed_text("bluetooth wireless headphones noise cancellation")
        c = embedder.embed_text("gardening tools for planting spring vegetables")
        sim_ab = sum(x * y for x, y in zip(a, b, strict=True))
        sim_ac = sum(x * y for x, y in zip(a, c, strict=True))
        assert sim_ab > sim_ac

    def test_batch_coverage_report(self):
        records = [
            {"_search_text": "some text"},
            {"_search_text": ""},
            {"_search_text": "more text"},
        ]
        _, report = HashingEmbedder(dim=32).embed_batch(records)
        assert report["embedded"] == 2
        assert report["skipped_empty_text"] == 1
        assert abs(report["coverage"] - 2 / 3) < 1e-9
