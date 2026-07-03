"""Schema validation + drift detection.

Failed records are quarantined (not dropped) so the pipeline degrades gracefully
instead of failing entirely — a core control-plane reliability principle.
"""

from __future__ import annotations

import logging
from typing import Any

from controlplane.models import ValidationResult, schema_fingerprint

logger = logging.getLogger(__name__)


class SchemaValidator:
    """Validates records against a lightweight JSON-schema-style contract.

    Schema format::

        {
          "required": ["id", "title"],
          "types": {"id": "str", "title": "str", "price": "number"},
          "constraints": {"price": {"min": 0}, "title": {"min_length": 1}}
        }
    """

    TYPE_MAP: dict[str, tuple[type, ...]] = {
        "str": (str,),
        "int": (int,),
        "float": (float, int),
        "number": (int, float),
        "bool": (bool,),
        "list": (list,),
        "dict": (dict,),
    }

    def __init__(self, schema: dict[str, Any]):
        self.required: list[str] = schema.get("required", [])
        self.types: dict[str, str] = schema.get("types", {})
        self.constraints: dict[str, dict[str, Any]] = schema.get("constraints", {})

    # ------------------------------------------------------------------ public
    def validate_batch(self, records: list[dict[str, Any]]) -> ValidationResult:
        valid: list[dict[str, Any]] = []
        quarantined: list[dict[str, Any]] = []

        for idx, record in enumerate(records):
            reasons = self._check_record(record)
            if reasons:
                quarantined.append(
                    {"record": record, "reason": "; ".join(reasons), "index": idx}
                )
            else:
                valid.append(record)

        result = ValidationResult(
            valid_records=valid,
            quarantined=quarantined,
            schema_hash=schema_fingerprint(records),
        )
        logger.info(
            "validation complete: %d valid, %d quarantined (pass_rate=%.2f%%)",
            len(valid),
            len(quarantined),
            result.pass_rate * 100,
        )
        return result

    # ----------------------------------------------------------------- private
    def _check_record(self, record: dict[str, Any]) -> list[str]:
        reasons: list[str] = []

        if not isinstance(record, dict):
            return ["record is not an object"]

        # required fields
        for field_name in self.required:
            if field_name not in record or record[field_name] in (None, ""):
                reasons.append(f"missing required field '{field_name}'")

        # type checks
        for field_name, expected in self.types.items():
            if field_name in record and record[field_name] is not None:
                allowed = self.TYPE_MAP.get(expected)
                if allowed and not isinstance(record[field_name], allowed):
                    reasons.append(
                        f"field '{field_name}' expected {expected}, "
                        f"got {type(record[field_name]).__name__}"
                    )

        # constraint checks
        for field_name, rules in self.constraints.items():
            value = record.get(field_name)
            if value is None:
                continue
            if "min" in rules and isinstance(value, (int, float)) and value < rules["min"]:
                reasons.append(f"field '{field_name}' below min {rules['min']}")
            if "max" in rules and isinstance(value, (int, float)) and value > rules["max"]:
                reasons.append(f"field '{field_name}' above max {rules['max']}")
            if "min_length" in rules and isinstance(value, str) and len(value) < rules["min_length"]:
                reasons.append(f"field '{field_name}' shorter than {rules['min_length']}")
            if "allowed" in rules and value not in rules["allowed"]:
                reasons.append(f"field '{field_name}' not in allowed set")

        return reasons


def detect_drift(previous_hash: str | None, current_hash: str) -> dict[str, Any]:
    """Compare schema fingerprints between the previous promoted version and now."""
    if previous_hash is None:
        return {"drifted": False, "reason": "no previous version (first ingest)"}
    drifted = previous_hash != current_hash
    return {
        "drifted": drifted,
        "previous_hash": previous_hash,
        "current_hash": current_hash,
        "reason": "schema fingerprint changed" if drifted else "schema stable",
    }
