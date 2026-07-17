"""Tests for the /mode endpoint (#2) and API-key auth on mutating routes (#11)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "gateway"))

from app import security as security_module  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def _set_api_key(monkeypatch, value: str) -> None:
    """Override the configured API key.

    ``Settings`` is a frozen dataclass, so we patch the resolver the security
    dependency reads through rather than the (immutable) settings field.
    """
    monkeypatch.setattr(security_module, "_configured_key", lambda: value)


class TestModeEndpoint:
    def test_mode_reports_demo_when_infra_absent(self, client):
        r = client.get("/mode")
        assert r.status_code == 200
        body = r.json()
        # In CI there is no redis/postgres/qdrant → demo mode.
        assert body["mode"] in ("demo", "degraded", "full")
        assert set(body["checks"]) == {"redis", "postgres", "qdrant"}


class TestApiKeyAuth:
    def test_open_mode_allows_reset(self, client, monkeypatch):
        # No API key configured → reset is open (demo mode).
        _set_api_key(monkeypatch, "")
        r = client.post("/demo/reset")
        assert r.status_code == 200

    def test_configured_key_blocks_unauthenticated_reset(self, client, monkeypatch):
        _set_api_key(monkeypatch, "s3cret")
        r = client.post("/demo/reset")
        assert r.status_code == 401

    def test_configured_key_allows_with_header(self, client, monkeypatch):
        _set_api_key(monkeypatch, "s3cret")
        r = client.post("/demo/reset", headers={"X-API-Key": "s3cret"})
        assert r.status_code == 200

    def test_read_endpoints_stay_open_with_key_set(self, client, monkeypatch):
        _set_api_key(monkeypatch, "s3cret")
        # Reads never require the key.
        assert client.get("/demo/stats").status_code == 200
        assert client.get("/demo/versions").status_code == 200
