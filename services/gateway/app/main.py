"""AI Data Control Plane — Ingestion Gateway.

The public front door of the platform:

* ``POST /ingest/upload``    — file upload → raw zone → event → Kestra flow
* ``POST /ingest/webhook``   — JSON payload webhook trigger
* ``GET  /versions``         — dataset version registry
* ``GET  /versions/{id}``    — full lineage: status, quality report, promotion
* ``POST /rollback/{ds}``    — one-click rollback of a dataset's prod alias
* ``GET  /search/{ds}``      — semantic search against the *prod alias* (proves promotion works)
* ``GET  /metrics``          — Prometheus metrics
* ``GET  /healthz``          — liveness + dependency checks
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from app.routers import console, demo, ingest, observability, registry, search
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from controlplane.config import settings

STATIC_DIR = Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

REQUEST_COUNT = Counter(
    "gateway_requests_total", "HTTP requests", ["method", "path", "status"]
)
REQUEST_LATENCY = Histogram(
    "gateway_request_duration_seconds", "Request latency", ["method", "path"]
)

app = FastAPI(
    title="AI Data Control Plane — Gateway",
    version="1.0.0",
    description="Event-driven ingestion gateway for the AI Data Control Plane.",
)

# CORS is locked down by default: with no CORS_ALLOW_ORIGINS configured the
# gateway is same-origin only (the console is served from the same origin, so it
# still works). Set CORS_ALLOW_ORIGINS to an explicit allowlist — or "*" for a
# throwaway public demo — to opt into cross-origin access.
_cors_origins = settings.cors_origins_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials="*" not in _cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    path = request.url.path
    # Avoid label cardinality explosion from dynamic path segments. Every route
    # with a path parameter — including the /demo/* twins — is normalised to a
    # single template so Prometheus labels stay bounded.
    for prefix in (
        "/versions/",
        "/rollback/",
        "/search/",
        "/demo/versions/",
        "/demo/rollback/",
        "/demo/search/",
    ):
        if path.startswith(prefix):
            path = prefix + "{param}"
            break
    REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(elapsed)
    return response


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(console.router, tags=["console"])
app.include_router(demo.router)
app.include_router(ingest.router, tags=["ingestion"])
app.include_router(registry.router, tags=["registry"])
app.include_router(search.router, tags=["search"])
app.include_router(observability.router, tags=["observability"])
