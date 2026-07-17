# AI Data Control Plane — Kestra task runner image
#
# Baking the runtime dependencies and the `controlplane` package into a
# pinned image means Kestra tasks no longer run `pip install ...` on every
# execution (issue #22): faster, deterministic, and network-outage-proof.
#
# Build & tag locally (or push to a registry your Kestra can reach):
#   docker build -f services/runner/Dockerfile -t control-plane/runner:1.0.0 .
#
# The flow's `runner_image` variable points at this tag; see
# flows/ingestion/dataset-pipeline.yaml.
FROM python:3.12-slim

WORKDIR /app

# Pin the exact runtime deps the flow tasks import. Kept in lock-step with
# services/gateway/requirements.txt so the runner and gateway agree.
RUN pip install --no-cache-dir \
    "minio>=7.2" \
    "psycopg[binary]>=3.2" \
    "psycopg_pool>=3.2" \
    "httpx>=0.27" \
    "redis>=5.0"

# Bake the shared control-plane library so `python -m controlplane.runner ...`
# works out of the box with no namespace-file sync or install step.
COPY controlplane/ ./controlplane/

# Kestra mounts namespace files over the working dir at runtime; keep the
# baked copy on PYTHONPATH so imports resolve regardless.
ENV PYTHONPATH=/app

CMD ["python", "-m", "controlplane.runner", "--help"]
