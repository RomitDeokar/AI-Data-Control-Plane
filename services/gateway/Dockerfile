# AI Data Control Plane — Ingestion Gateway
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared control-plane library + gateway app (includes app/static/console.html)
COPY controlplane/ ./controlplane/
COPY services/gateway/app/ ./app/

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# NOTE: pinned to a single worker on purpose. The in-memory demo engine
# (controlplane.demo.demo_engine) keeps all demo state in one process; running
# multiple workers would give each its own copy and surface inconsistent
# stats/versions. Scale horizontally only after moving demo state to Redis.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
