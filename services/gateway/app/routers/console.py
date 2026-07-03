"""Serves the single-page Control Plane Console.

A dependency-free vanilla-JS dashboard that visualizes the live state of the
platform (versions, promotions, event-bus depth) and lets you run searches and
rollbacks from the browser. Perfect for demos and portfolio screenshots.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_CONSOLE_HTML = (Path(__file__).parent.parent / "static" / "console.html").read_text()


@router.get("/", include_in_schema=False, response_class=HTMLResponse)
def console() -> str:
    """The Control Plane Console (single-page dashboard)."""
    return _CONSOLE_HTML
