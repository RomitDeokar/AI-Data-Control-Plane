"""AI Data Control Plane — core orchestration library.

Modules
-------
validation   : schema validation, drift detection, record quarantining
enrichment   : metadata enrichment, normalization, deduplication
embeddings   : deterministic embedding / feature generation
quality      : quality gates (completeness, uniqueness, coverage, drift)
promotion    : blue/green promotion engine with rollback support
stores       : Postgres registry, Qdrant vector store, MinIO object store adapters
"""

__version__ = "1.0.0"
