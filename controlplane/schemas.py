"""Single source of truth for dataset contracts (schemas).

Both the Kestra pipeline runner (:mod:`controlplane.runner`) and the in-memory
demo engine (:mod:`controlplane.demo`) consume these definitions. Keeping them
here avoids the copy-paste drift that used to exist between ``DEFAULT_SCHEMAS``
and ``DEMO_SCHEMAS``.

In a full production deployment these contracts live in the ``schema_registry``
table; this module is the fallback used for first-run bootstrapping and for the
zero-infrastructure demo.
"""

from __future__ import annotations

from typing import Any

# Dataset contracts: required fields, expected types, and per-field constraints.
DATASET_SCHEMAS: dict[str, dict[str, Any]] = {
    "products": {
        "required": ["id", "title", "category", "price"],
        "types": {"id": "str", "title": "str", "category": "str", "price": "number"},
        "constraints": {"price": {"min": 0}, "title": {"min_length": 2}},
    },
    "documents": {
        "required": ["id", "title", "content"],
        "types": {"id": "str", "title": "str", "content": "str"},
        "constraints": {"content": {"min_length": 10}},
    },
}

# Fallback contract for an unknown dataset: only require an ``id`` field.
FALLBACK_SCHEMA: dict[str, Any] = {"required": ["id"], "types": {}, "constraints": {}}


def schema_for(dataset: str) -> dict[str, Any]:
    """Return the contract for ``dataset`` (or the permissive fallback)."""
    return DATASET_SCHEMAS.get(dataset, FALLBACK_SCHEMA)
