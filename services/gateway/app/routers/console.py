"""Serves the single-page Control Plane Console.

A dependency-free vanilla-JS dashboard that visualizes the live state of the
platform (versions, promotions, event-bus depth) and lets you run searches and
rollbacks from the browser. Perfect for demos and portfolio screenshots.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_CONSOLE_HTML = Path(__file__).parent.parent / "static" / "console.html"


@router.get("/", include_in_schema=False)
def console() -> FileResponse:
    """The Control Plane Console (single-page dashboard).

    Served via FileResponse (read per request) rather than slurped once at
    import time, so edits to console.html are picked up without restarting the
    server during development.
    """
    return FileResponse(_CONSOLE_HTML, media_type="text/html")
