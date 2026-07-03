"""Deterministic hashing-based text embeddings.

Design decision
---------------
We use a *feature-hashing* embedder (no external model download, no GPU, no API
key) so the platform runs anywhere and CI stays fast. The interface matches what
you'd use for a real model — swap ``HashingEmbedder`` for a SentenceTransformer
or OpenAI-backed embedder by implementing ``embed_batch()`` with the same
signature. The control plane doesn't care *how* vectors are produced; it cares
about coverage, dimensions, and versioned promotion.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Feature-hashing embedder with L2 normalization (cosine-similarity ready)."""

    def __init__(self, dim: int = 256, model_name: str = "hashing-tfidf-v1"):
        self.dim = dim
        self.model_name = model_name

    # ------------------------------------------------------------------ public
    def embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            return vector

        # term frequency with signed feature hashing
        for token in tokens:
            digest = hashlib.md5(token.encode()).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        # sublinear tf scaling
        vector = [math.copysign(math.log1p(abs(v)), v) if v else 0.0 for v in vector]

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector

    def embed_batch(
        self, records: list[dict[str, Any]], text_field: str = "_search_text"
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Attach ``_vector`` to each record. Returns (records, coverage_report)."""
        embedded = 0
        skipped = 0
        for record in records:
            text = record.get(text_field) or ""
            if text.strip():
                record["_vector"] = self.embed_text(text)
                embedded += 1
            else:
                record["_vector"] = None
                skipped += 1

        total = len(records)
        report = {
            "model": self.model_name,
            "dim": self.dim,
            "total": total,
            "embedded": embedded,
            "skipped_empty_text": skipped,
            "coverage": embedded / total if total else 0.0,
        }
        logger.info("embedding complete: %s", report)
        return records, report
