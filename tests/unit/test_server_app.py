"""Tests for server.app: dev-mode root + static-mounted PWA fallback."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_root_returns_dev_message_when_no_static_dir(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MORNING_DIGEST_DATA_DIR", str(tmp_data_dir))
    # Point static_dir at a path that doesn't exist; the app should mount the
    # dev JSON note instead of FileResponse.
    monkeypatch.setenv("MORNING_DIGEST_STATIC_DIR", str(tmp_path / "no-static-here"))
    from server.app import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "dev"
        assert "PWA not built" in body["note"]


def test_root_and_spa_fallback_when_static_present(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When server/static has an index.html, the app mounts /assets, serves
    index.html at /, and falls back to index.html for any unknown SPA path.
    A real asset file is served directly."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><h1>Dispatch</h1>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    (static / "manifest.webmanifest").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("MORNING_DIGEST_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("MORNING_DIGEST_STATIC_DIR", str(static))

    from server.app import create_app

    app = create_app()
    with TestClient(app) as c:
        root = c.get("/")
        assert root.status_code == 200
        assert "Dispatch" in root.text

        # File that exists on disk (not under /assets) goes through SPA fallback's
        # is_file() branch and is served directly.
        manifest = c.get("/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.text == "{}"

        # /assets is a StaticFiles mount.
        asset = c.get("/assets/app.js")
        assert asset.status_code == 200
        assert "console.log" in asset.text

        # Unknown path falls back to index.html for client-side routing.
        deep = c.get("/digests/2026-05-14")
        assert deep.status_code == 200
        assert "Dispatch" in deep.text
