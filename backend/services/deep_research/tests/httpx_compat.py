"""Compatibility helpers for httpx test clients."""

from __future__ import annotations

from httpx import ASGITransport


def create_asgi_transport(*, app):
    """Build an ASGI transport compatible with multiple httpx versions."""

    try:
        return ASGITransport(app=app, lifespan="on")
    except TypeError:
        return ASGITransport(app=app)
