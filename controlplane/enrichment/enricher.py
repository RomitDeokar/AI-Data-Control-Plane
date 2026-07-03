"""Metadata enrichment: normalization, deduplication, derived fields, lineage tags.

Runs after validation, before embedding generation.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


class Enricher:
    """Pure-Python, deterministic enrichment so results are reproducible."""

    def __init__(self, text_fields: list[str] | None = None, key_field: str = "id"):
        self.text_fields = text_fields or ["title", "description", "content", "text"]
        self.key_field = key_field

    # ------------------------------------------------------------------ public
    def enrich_batch(
        self, records: list[dict[str, Any]], version_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return (enriched_records, enrichment_report)."""
        seen_keys: set[str] = set()
        enriched: list[dict[str, Any]] = []
        duplicates = 0

        for record in records:
            key = str(record.get(self.key_field, ""))
            content_key = key or self._content_hash(record)
            if content_key in seen_keys:
                duplicates += 1
                continue
            seen_keys.add(content_key)
            enriched.append(self._enrich_record(record, version_id))

        report = {
            "input_records": len(records),
            "output_records": len(enriched),
            "duplicates_removed": duplicates,
            "enriched_at": datetime.now(UTC).isoformat(),
        }
        logger.info("enrichment complete: %s", report)
        return enriched, report

    # ----------------------------------------------------------------- private
    def _enrich_record(self, record: dict[str, Any], version_id: str) -> dict[str, Any]:
        out = dict(record)

        # 1. normalize text fields (unicode NFC + collapse whitespace)
        for field_name in self.text_fields:
            value = out.get(field_name)
            if isinstance(value, str):
                out[field_name] = _WHITESPACE_RE.sub(
                    " ", unicodedata.normalize("NFC", value)
                ).strip()

        # 2. build a combined searchable text blob
        parts = [str(out[f]) for f in self.text_fields if out.get(f)]
        out["_search_text"] = " | ".join(parts)[:4096]

        # 3. derived metadata
        out["_meta"] = {
            "version_id": version_id,
            "content_hash": self._content_hash(record),
            "text_length": len(out["_search_text"]),
            "field_count": len(record),
            "enriched_at": datetime.now(UTC).isoformat(),
        }
        return out

    @staticmethod
    def _content_hash(record: dict[str, Any]) -> str:
        canonical = repr(sorted(record.items()))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
