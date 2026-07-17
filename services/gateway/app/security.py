"""API-key authentication for mutating gateway endpoints.

Design
------
Mutating endpoints (upload, webhook, rollback, demo reset) accept an optional
API key via the ``X-API-Key`` header. Behaviour is controlled by the
``CONTROLPLANE_API_KEY`` setting:

* **empty** (default) — auth is disabled; the platform runs in open *demo mode*
  so a portfolio visitor can drive it without credentials.
* **set** — the header must be present and match, or the request is rejected
  with ``401``.

Read-only endpoints (versions, promotions, search, healthz, metrics) are always
open so the console and dashboards work without a key.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from controlplane.config import settings

API_KEY_HEADER = "X-API-Key"


def _configured_key() -> str:
    """Return the configured API key.

    Wrapped in a function so it is resolved at request time (not import time)
    and can be cleanly overridden in tests — ``Settings`` is a frozen dataclass,
    so patching ``settings.api_key`` directly is not possible.
    """
    return settings.api_key


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency enforcing the API key on mutating endpoints.

    No-op when ``CONTROLPLANE_API_KEY`` is unset (open demo mode).
    """
    expected = _configured_key()
    if not expected:
        return  # open demo mode — auth disabled
    if not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API key (set the X-API-Key header)",
            headers={"WWW-Authenticate": "ApiKey"},
        )
