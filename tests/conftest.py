"""Shared fixtures: in-memory fakes for the vector store and registry so the
core control-plane logic is tested without any running infrastructure."""

from __future__ import annotations

from typing import Any

import pytest


class FakeVectorStore:
    """In-memory stand-in for QdrantStore implementing the promotion interface."""

    def __init__(self):
        self.collections: dict[str, list[dict[str, Any]]] = {}
        self.aliases: dict[str, str] = {}

    def create_collection(self, name: str, dim: int) -> None:
        self.collections.setdefault(name, [])

    def drop_collection(self, name: str) -> None:
        self.collections.pop(name, None)

    def list_collections(self) -> list[str]:
        return list(self.collections)

    def upsert(self, collection: str, points: list[dict[str, Any]], batch_size: int = 256) -> int:
        self.collections[collection].extend(points)
        return len(points)

    def set_alias(self, alias: str, collection: str) -> None:
        self.aliases[alias] = collection

    def get_alias_target(self, alias: str) -> str | None:
        return self.aliases.get(alias)


class FakeRegistry:
    """In-memory stand-in for MetadataRegistry."""

    def __init__(self):
        self.versions: dict[str, dict[str, Any]] = {}
        self.promotions: list[dict[str, Any]] = []
        self.quality_reports: list[Any] = []
        self.quarantined: list[dict[str, Any]] = []

    def register_version(self, version) -> None:
        self.versions[version.version_id] = {
            "dataset": version.dataset,
            "version_id": version.version_id,
            "status": version.status,
        }

    def update_version_status(self, version_id: str, status: str, record_count=None) -> None:
        self.versions.setdefault(version_id, {"version_id": version_id})["status"] = status

    def record_promotion(self, **kwargs) -> None:
        self.promotions.append(kwargs)

    def record_quality_report(self, verdict) -> None:
        self.quality_reports.append(verdict)

    def get_promotion_history(self, dataset: str, limit: int = 50) -> list[str]:
        seen: set[str] = set()
        history: list[str] = []
        for entry in reversed(self.promotions):
            if entry.get("decision") != "promoted":
                continue
            vid = entry.get("version_id")
            if vid in seen:
                continue
            seen.add(vid)
            history.append(vid)
        return history

    def get_previous_promoted(self, dataset: str, exclude_current: str | None) -> str | None:
        exclude = (exclude_current or "").replace(f"{dataset}__", "", 1)
        for vid in self.get_promotion_history(dataset):
            if vid != exclude:
                return vid
        return None


@pytest.fixture
def fake_vector_store():
    return FakeVectorStore()


@pytest.fixture
def fake_registry():
    return FakeRegistry()


@pytest.fixture
def product_schema():
    return {
        "required": ["id", "title", "category", "price"],
        "types": {"id": "str", "title": "str", "category": "str", "price": "number"},
        "constraints": {"price": {"min": 0}, "title": {"min_length": 2}},
    }


@pytest.fixture
def good_records():
    return [
        {"id": f"P{i}", "title": f"Product {i}", "category": "electronics", "price": 10.0 + i}
        for i in range(20)
    ]
