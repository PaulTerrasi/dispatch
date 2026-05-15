"""BearerAuthMiddleware: protects /api and /internal when a token is set."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.middleware import BearerAuthMiddleware


def _app_with_token(token: str | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=token)

    @app.get("/api/ping")
    def api_ping() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/internal/ping")
    def internal_ping() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/public")
    def public() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_no_token_means_open() -> None:
    """Local dev (no MORNING_DIGEST_AUTH_TOKEN set) skips auth entirely."""
    with TestClient(_app_with_token(None)) as c:
        assert c.get("/api/ping").status_code == 200
        assert c.get("/internal/ping").status_code == 200
        assert c.get("/public").status_code == 200


def test_missing_bearer_header_rejected() -> None:
    with TestClient(_app_with_token("sekret")) as c:
        r = c.get("/api/ping")
        assert r.status_code == 401
        assert r.text == "Unauthorized"


def test_wrong_token_rejected() -> None:
    with TestClient(_app_with_token("sekret")) as c:
        r = c.get("/api/ping", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401


def test_non_bearer_scheme_rejected() -> None:
    with TestClient(_app_with_token("sekret")) as c:
        r = c.get("/api/ping", headers={"Authorization": "Basic c2VrcmV0"})
        assert r.status_code == 401


def test_correct_token_passes() -> None:
    with TestClient(_app_with_token("sekret")) as c:
        r = c.get("/api/ping", headers={"Authorization": "Bearer sekret"})
        assert r.status_code == 200


def test_internal_prefix_also_protected() -> None:
    with TestClient(_app_with_token("sekret")) as c:
        assert c.get("/internal/ping").status_code == 401
        r = c.get("/internal/ping", headers={"Authorization": "Bearer sekret"})
        assert r.status_code == 200


def test_public_routes_not_protected_when_token_set() -> None:
    """Auth gate is scoped to /api and /internal — root paths are open
    so the PWA assets and SPA fallback load."""
    with TestClient(_app_with_token("sekret")) as c:
        assert c.get("/public").status_code == 200
