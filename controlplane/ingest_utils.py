"""Shared ingestion helpers: robust record parsing + input sanitisation.

Centralising these avoids subtle divergence between the gateway front door
(:mod:`services.gateway.app.routers.ingest`), the Kestra runner
(:mod:`controlplane.runner`), and the demo sample loader.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Datasets, filenames and any other value that flows into object-storage keys,
# Qdrant collection names, or Prometheus labels must match this. Rejecting
# everything else closes path-traversal (``../../evil``), label-cardinality and
# injection vectors in one place.
_IDENTIFIER_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
_UNSAFE_KEY_CHARS = re.compile(r"[^a-zA-Z0-9._-]")


class IngestError(ValueError):
    """Raised when a payload cannot be parsed or an identifier is invalid."""


def validate_identifier(value: str, *, field: str = "dataset") -> str:
    """Return ``value`` if it is a safe identifier, else raise ``IngestError``.

    Safe means ``^[a-z0-9_-]{1,64}$`` — lowercase alphanumerics, dashes and
    underscores only. This is what dataset names must satisfy before being used
    in bucket paths, collection names or metric labels.
    """
    if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
        raise IngestError(
            f"invalid {field} '{value}': must match ^[a-z0-9_-]{{1,64}}$"
        )
    return value


def sanitize_filename(filename: str) -> str:
    """Reduce an uploaded filename to a safe basename for an object key.

    Strips any directory components and replaces unsafe characters, so a
    filename like ``../../evil.json`` becomes ``evil.json`` and can never
    produce a traversal-looking or ambiguous object key.
    """
    # Take the basename only — drop any path the client tried to smuggle in.
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE_KEY_CHARS.sub("_", base).lstrip(".") or "upload"
    return base[:128]


def parse_records(text: str) -> list[dict[str, Any]]:
    """Parse a JSON/JSONL payload into a list of record dicts.

    Order of attempts (fixes the multi-line pretty-printed object bug):

    1. Try to parse the whole document as JSON. A list is returned as-is; a
       single object is wrapped into a one-element list. This handles both
       compact and *pretty-printed / multi-line* JSON objects and arrays.
    2. Only if full-document parsing fails do we fall back to JSONL, parsing
       each non-empty line as its own JSON object.

    Raises ``IngestError`` on invalid payloads.
    """
    stripped = text.strip()
    if not stripped:
        raise IngestError("payload is empty")

    # 1. whole-document parse (handles multi-line objects/arrays)
    try:
        doc = json.loads(stripped)
    except json.JSONDecodeError:
        doc = None
    else:
        if isinstance(doc, list):
            return [r for r in doc]
        if isinstance(doc, dict):
            return [doc]
        raise IngestError("top-level JSON must be an object or array")

    # 2. JSONL fallback — every non-empty line is its own object
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(stripped.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise IngestError(f"invalid JSON on line {lineno}: {exc}") from exc
    if not records:
        raise IngestError("payload contains no records")
    return records
