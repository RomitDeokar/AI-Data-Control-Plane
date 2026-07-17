"""Qdrant vector store adapter (REST API via httpx — no heavy client dependency).

Implements the interface expected by :class:`controlplane.promotion.PromotionEngine`,
including alias-based blue/green switching.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from controlplane.config import settings

logger = logging.getLogger(__name__)


class QdrantStore:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        self.base_url = (base_url or settings.qdrant_url).rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)

    # ----------------------------------------------------------- collections
    def create_collection(self, name: str, dim: int) -> None:
        response = self.client.put(
            f"/collections/{name}",
            json={"vectors": {"size": dim, "distance": "Cosine"}},
        )
        if response.status_code not in (200, 409):
            response.raise_for_status()

    def drop_collection(self, name: str) -> None:
        self.client.delete(f"/collections/{name}")

    def list_collections(self) -> list[str]:
        response = self.client.get("/collections")
        response.raise_for_status()
        return [c["name"] for c in response.json()["result"]["collections"]]

    def count(self, name: str) -> int:
        response = self.client.post(f"/collections/{name}/points/count", json={"exact": True})
        response.raise_for_status()
        return response.json()["result"]["count"]

    # ----------------------------------------------------------------- points
    def upsert(self, collection: str, points: list[dict[str, Any]], batch_size: int = 256) -> int:
        total = 0
        for start in range(0, len(points), batch_size):
            batch = points[start : start + batch_size]
            response = self.client.put(
                f"/collections/{collection}/points?wait=true",
                json={"points": batch},
            )
            response.raise_for_status()
            total += len(batch)
        return total

    def search(
        self, collection_or_alias: str, vector: list[float], limit: int = 5
    ) -> list[dict[str, Any]]:
        response = self.client.post(
            f"/collections/{collection_or_alias}/points/search",
            json={"vector": vector, "limit": limit, "with_payload": True},
        )
        response.raise_for_status()
        return response.json()["result"]

    # ---------------------------------------------------------------- aliases
    def set_alias(self, alias: str, collection: str) -> None:
        """Re-point alias → collection (blue/green switch).

        Qdrant applies all actions in a single ``/collections/aliases`` request
        atomically. When the alias already exists we send delete+create together
        so there is never a window where the alias is absent. On the *first*
        promotion the alias does not exist yet, so we detect that case up front
        (by inspecting the current alias target) and send create-only — rather
        than firing a delete that errors and then retrying, which is what opened
        a brief "alias missing → search 404" window before.
        """
        alias_exists = self.get_alias_target(alias) is not None
        actions: list[dict[str, Any]] = []
        if alias_exists:
            actions.append({"delete_alias": {"alias_name": alias}})
        actions.append(
            {"create_alias": {"alias_name": alias, "collection_name": collection}}
        )
        response = self.client.post("/collections/aliases", json={"actions": actions})
        response.raise_for_status()
        logger.info("alias %s → %s", alias, collection)

    def get_alias_target(self, alias: str) -> str | None:
        response = self.client.get("/aliases")
        response.raise_for_status()
        for entry in response.json()["result"]["aliases"]:
            if entry["alias_name"] == alias:
                return entry["collection_name"]
        return None
