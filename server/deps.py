"""FastAPI dependencies — single source of truth for cross-route helpers."""

from __future__ import annotations

from fastapi import Request

from digest.store_protocol import StoreProtocol


def get_store(request: Request) -> StoreProtocol:
    """Return the store attached to the app at startup (filesystem or S3)."""
    store: StoreProtocol = request.app.state.store
    return store
