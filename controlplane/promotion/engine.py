"""Blue/green promotion engine with rollback.

Strategy
--------
Each dataset version is upserted into its own Qdrant collection
(``{dataset}__{version_id}``). Production traffic reads through a stable alias
(``{dataset}__prod``). Promotion = atomically re-pointing the alias to the new
collection. Rollback = re-pointing back to the previous one. Old collections
are retained for N versions so rollback is instant and zero-copy.
"""

from __future__ import annotations

import logging
from typing import Any

from controlplane.models import GateVerdict

logger = logging.getLogger(__name__)


class PromotionEngine:
    """Coordinates gate verdicts, the vector store, and the metadata registry."""

    RETAIN_VERSIONS = 3  # keep this many old collections for instant rollback

    def __init__(self, vector_store: Any, registry: Any):
        """
        Parameters
        ----------
        vector_store : object implementing
            ``create_collection``, ``upsert``, ``set_alias``, ``get_alias_target``,
            ``list_collections``, ``drop_collection``
        registry : object implementing
            ``record_promotion``, ``update_version_status``, ``get_previous_promoted``
        """
        self.vector_store = vector_store
        self.registry = registry

    # ------------------------------------------------------------------ public
    def stage(
        self, dataset: str, version_id: str, records: list[dict[str, Any]], dim: int
    ) -> str:
        """Write embedded records into a version-scoped staging collection."""
        collection = f"{dataset}__{version_id}"
        self.vector_store.create_collection(collection, dim)
        points = [
            {
                "id": idx,
                "vector": rec["_vector"],
                "payload": {k: v for k, v in rec.items() if k != "_vector"},
            }
            for idx, rec in enumerate(records)
            if rec.get("_vector")
        ]
        self.vector_store.upsert(collection, points)
        logger.info("staged %d points into %s", len(points), collection)
        return collection

    def decide(self, dataset: str, version_id: str, verdict: GateVerdict) -> dict[str, Any]:
        """Promote if all gates passed, otherwise reject. Returns a decision report."""
        alias = f"{dataset}__prod"
        new_collection = f"{dataset}__{version_id}"
        previous = self.vector_store.get_alias_target(alias)

        if verdict.passed:
            self.vector_store.set_alias(alias, new_collection)
            self.registry.record_promotion(
                version_id=version_id,
                decision="promoted",
                from_target=previous,
                to_target=new_collection,
                reason="all quality gates passed",
                gate_summary=verdict.summary,
            )
            self.registry.update_version_status(version_id, "promoted")
            self._cleanup_old_versions(dataset, keep_current=new_collection, keep_prev=previous)
            decision = {
                "decision": "promoted",
                "alias": alias,
                "now_serving": new_collection,
                "previous": previous,
                "rollback_available": previous is not None,
            }
        else:
            failed = [c.check_name for c in verdict.checks if not c.passed]
            self.registry.record_promotion(
                version_id=version_id,
                decision="rejected",
                from_target=previous,
                to_target=None,
                reason=f"failed gates: {', '.join(failed)}",
                gate_summary=verdict.summary,
            )
            self.registry.update_version_status(version_id, "rejected")
            decision = {
                "decision": "rejected",
                "alias": alias,
                "still_serving": previous,
                "failed_gates": failed,
            }

        logger.info("promotion decision for %s: %s", version_id, decision["decision"])
        return decision

    def rollback(self, dataset: str, reason: str = "manual rollback") -> dict[str, Any]:
        """Re-point the prod alias to the version promoted *immediately before*
        the one currently serving.

        The promotion ledger is the source of truth. ``get_promotion_history``
        returns promoted versions newest-first and is stable across rollbacks
        (rollback events carry ``decision="rolled_back"`` and are excluded from
        that view), so it gives a deterministic chain ``[v3, v2, v1]``.

        We locate the currently-serving version in that chain and step *one
        position older* — then keep stepping while the candidate's collection
        has been dropped by the retention window (:attr:`RETAIN_VERSIONS`). This
        prevents two failure modes:

        * **Bouncing** — picking "newest promoted that isn't current" made a
          second rollback jump back to the version we just left. Stepping by
          ledger position instead walks v3 → v2 → v1 monotonically.
        * **Dead alias** — rollback never targets a collection that no longer
          exists, so the prod alias never points at nothing.
        """
        alias = f"{dataset}__prod"
        current = self.vector_store.get_alias_target(alias)
        current_version = (
            current.replace(f"{dataset}__", "", 1) if current else None
        )

        existing = set(self.vector_store.list_collections())
        history = self.registry.get_promotion_history(dataset)

        # Find where the currently-serving version sits in the ledger, then
        # consider everything strictly older than it. If the current version
        # isn't in the promoted history (edge case), fall back to the whole
        # history so we still have a candidate list.
        try:
            start = history.index(current_version) + 1  # type: ignore[arg-type]
        except (ValueError, TypeError):
            start = 0

        target_version: str | None = None
        for candidate in history[start:]:
            if candidate == current_version:
                continue
            if f"{dataset}__{candidate}" in existing:
                target_version = candidate
                break

        if target_version is None:
            return {
                "decision": "rollback_failed",
                "reason": "no previous promoted version with a live collection",
            }

        target = f"{dataset}__{target_version}"
        self.vector_store.set_alias(alias, target)
        self.registry.record_promotion(
            version_id=target_version,
            decision="rolled_back",
            from_target=current,
            to_target=target,
            reason=reason,
            gate_summary={},
        )
        if current_version:
            self.registry.update_version_status(current_version, "rolled_back")

        return {"decision": "rolled_back", "alias": alias, "now_serving": target}

    # ----------------------------------------------------------------- private
    def _cleanup_old_versions(
        self, dataset: str, keep_current: str, keep_prev: str | None
    ) -> None:
        """Drop version collections beyond the retention window."""
        prefix = f"{dataset}__"
        collections = sorted(
            c for c in self.vector_store.list_collections()
            if c.startswith(prefix) and not c.endswith("__prod")
        )
        keep = {keep_current}
        if keep_prev:
            keep.add(keep_prev)
        candidates = [c for c in collections if c not in keep]
        excess = len(candidates) - (self.RETAIN_VERSIONS - len(keep))
        for collection in candidates[: max(0, excess)]:
            self.vector_store.drop_collection(collection)
            logger.info("dropped expired collection %s", collection)
