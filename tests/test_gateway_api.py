"""Gateway API contract tests using FastAPI's TestClient.

Infrastructure-dependent behavior is monkeypatched so these run in CI without
Docker; they assert the HTTP contract (status codes, payload shapes, validation).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "gateway"))

from app.main import app  # noqa: E402
from app.routers import ingest as ingest_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def stub_infra(monkeypatch):
    """Stub out MinIO / Redis / Kestra so ingestion endpoints run in-memory."""

    class StubStore:
        def write_raw(self, dataset, filename, data, content_type):
            return f"s3://raw/{dataset}/{filename}"

    class StubBus:
        published: list = []

        def publish(self, event_type, payload):
            StubBus.published.append((event_type, payload))
            return "1-0"

    monkeypatch.setattr(ingest_module, "ObjectStore", lambda: StubStore())
    monkeypatch.setattr(ingest_module, "EventBus", lambda: StubBus())
    monkeypatch.setattr(
        ingest_module,
        "_trigger_kestra_flow",
        lambda dataset, source_uri, trigger_type: {"triggered": True, "execution_id": "test-exec"},
    )
    StubBus.published = []
    return StubBus


class TestRootAndHealth:
    def test_root_serves_console(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "AI Data Control Plane" in response.text

    def test_healthz_reports_dependency_status(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("healthy", "degraded")
        assert set(body["checks"]) == {"redis", "postgres", "qdrant"}

    def test_metrics_exposed(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "gateway_requests_total" in response.text


class TestUploadValidation:
    def test_rejects_non_json_extension(self, client):
        response = client.post(
            "/ingest/upload",
            data={"dataset": "products"},
            files={"file": ("data.csv", b"a,b,c", "text/csv")},
        )
        assert response.status_code == 400

    def test_rejects_malformed_json(self, client, stub_infra):
        response = client.post(
            "/ingest/upload",
            data={"dataset": "products"},
            files={"file": ("data.json", b"{not json", "application/json")},
        )
        assert response.status_code == 400

    def test_accepts_valid_json_and_publishes_event(self, client, stub_infra):
        response = client.post(
            "/ingest/upload",
            data={"dataset": "products"},
            files={"file": ("data.json", b'[{"id": "P1", "title": "X"}]', "application/json")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "accepted"
        assert body["record_count"] == 1
        assert body["kestra"]["triggered"] is True
        assert stub_infra.published[0][0] == "dataset.ingested"

    def test_accepts_jsonl(self, client, stub_infra):
        payload = b'{"id": "1"}\n{"id": "2"}\n'
        response = client.post(
            "/ingest/upload",
            data={"dataset": "products"},
            files={"file": ("data.jsonl", payload, "application/x-ndjson")},
        )
        assert response.status_code == 200
        assert response.json()["record_count"] == 2


class TestWebhook:
    def test_webhook_requires_records(self, client):
        response = client.post("/ingest/webhook", json={"dataset": "products", "records": []})
        assert response.status_code == 422

    def test_webhook_accepts_records(self, client, stub_infra):
        response = client.post(
            "/ingest/webhook",
            json={"dataset": "documents", "records": [{"id": "D1", "title": "Doc"}]},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

    def test_webhook_idempotency_key_derived_from_content(self, client, stub_infra):
        payload = {"dataset": "documents", "records": [{"id": "D1"}]}
        client.post("/ingest/webhook", json=payload)
        event_payload = stub_infra.published[0][1]
        assert "idempotency_key" in event_payload
        assert event_payload["trigger_type"] == "webhook"
