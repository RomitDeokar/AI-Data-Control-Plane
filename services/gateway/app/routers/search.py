"""Semantic search against the production alias.

This endpoint proves the promotion engine works: it always queries
``{dataset}__prod`` — whatever collection the alias currently points at.
Promote a new version → results change. Roll back → results revert.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from controlplane.config import settings
from controlplane.embeddings import HashingEmbedder
from controlplane.stores import QdrantStore

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/search/{dataset}")
def semantic_search(dataset: str, q: str, limit: int = 5) -> dict[str, Any]:
    if not q.strip():
        raise HTTPException(400, "query 'q' must not be empty")

    embedder = HashingEmbedder(dim=settings.embedding_dim, model_name=settings.embedding_model)
    vector = embedder.embed_text(q)

    store = QdrantStore()
    alias = f"{dataset}__prod"
    try:
        hits = store.search(alias, vector, limit=min(limit, 25))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                404,
                f"no production data for dataset '{dataset}' — nothing promoted yet",
            ) from exc
        raise HTTPException(502, "vector store error") from exc

    serving = store.get_alias_target(alias)
    return {
        "dataset": dataset,
        "query": q,
        "serving_collection": serving,
        "results": [
            {
                "score": round(hit["score"], 4),
                "payload": {
                    k: v for k, v in hit.get("payload", {}).items() if not k.startswith("_")
                },
                "version_id": hit.get("payload", {}).get("_meta", {}).get("version_id"),
            }
            for hit in hits
        ],
    }
