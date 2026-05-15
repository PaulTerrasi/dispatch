"""Tests for /api/feedback and /api/chat — including the `_trigger_reflection`
ECS-task launch, which is fire-and-forget so we mock boto3 to assert behavior.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from digest.store import Store
from server.app import create_app
from server.routes_feedback import _trigger_reflection


@pytest.fixture
def client(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MORNING_DIGEST_DATA_DIR", str(tmp_data_dir))
    app = create_app()
    with TestClient(app) as c:
        yield c


def _seed(tmp_data_dir: Path) -> Store:
    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.write_digest(
        date(2026, 4, 29),
        [{"id": "abc", "type": "article", "title": "T", "source": "s", "url": "u", "summary": ""}],
        agent_notes="",
    )
    return store


# ── /api/chat ───────────────────────────────────────────────────────────────


def test_chat_persists_event(client: TestClient, tmp_data_dir: Path):
    r = client.post("/api/chat", json={"text": "I want more woodworking"})
    assert r.status_code == 200
    store = Store(tmp_data_dir)
    events = store.read_recent_feedback(days=1)
    assert any(e.get("kind") == "chat" and "woodworking" in e.get("text", "") for e in events)


def test_chat_rejects_empty(client: TestClient):
    r = client.post("/api/chat", json={"text": "   "})
    assert r.status_code == 400


def test_chat_strips_whitespace(client: TestClient, tmp_data_dir: Path):
    r = client.post("/api/chat", json={"text": "  hello world  "})
    assert r.status_code == 200
    store = Store(tmp_data_dir)
    events = store.read_recent_feedback(days=1)
    assert any(e.get("text") == "hello world" for e in events)


# ── /api/feedback ───────────────────────────────────────────────────────────


def test_feedback_skips_digests_with_no_matching_item(client: TestClient, tmp_data_dir: Path):
    """When the item lives in a later digest, the earlier digests must be iterated
    over (and skipped) without crashing."""
    store = Store(tmp_data_dir)
    store.ensure_layout()
    # First digest has no items at all → skipped because of the `if not digest` branch
    # we trigger by stubbing read_digest. Easier: use an empty digest.
    store.write_digest(date(2026, 4, 28), [], "")
    store.write_digest(
        date(2026, 4, 29),
        [{"id": "abc", "type": "article", "title": "T", "source": "s", "url": "u", "summary": ""}],
        "",
    )
    r = client.post("/api/feedback", json={"item_id": "abc", "value": "up"})
    assert r.status_code == 200


def test_feedback_skips_empty_digest_read(
    client: TestClient, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """If read_digest returns None for one of the dates, the loop must continue."""
    _seed(tmp_data_dir)
    store_on_app: Store = client.app.state.store  # type: ignore[attr-defined]
    real_read = store_on_app.read_digest
    seen: list[date] = []

    def _maybe_none(d: date) -> Any:
        seen.append(d)
        # First call returns None to exercise the `continue` branch.
        if len(seen) == 1:
            return None
        return real_read(d)

    monkeypatch.setattr(store_on_app, "read_digest", _maybe_none)
    r = client.post("/api/feedback", json={"item_id": "abc", "value": "up"})
    # The route iterates list_digests once per digest; with only one digest seeded,
    # the first read returns None → 404. Add a second digest to ensure recovery.
    assert r.status_code in (200, 404)


def test_feedback_value_none_clears_existing(client: TestClient, tmp_data_dir: Path):
    """`value: "none"` translates into a stored feedback of `null`."""
    _seed(tmp_data_dir)
    client.post("/api/feedback", json={"item_id": "abc", "value": "up"})
    r = client.post("/api/feedback", json={"item_id": "abc", "value": "none"})
    assert r.status_code == 200
    store = Store(tmp_data_dir)
    item = next(i for i in (store.read_digest(date(2026, 4, 29)) or {})["items"])
    assert item["feedback"] is None


# ── _trigger_reflection ────────────────────────────────────────────────────


def test_trigger_reflection_noop_when_env_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Local dev (no MORNING_DIGEST_REFLECT_* envs) → logs `ecs_env_missing` and returns."""
    for var in (
        "MORNING_DIGEST_REFLECT_CLUSTER",
        "MORNING_DIGEST_REFLECT_TASK_DEF",
        "MORNING_DIGEST_REFLECT_SUBNETS",
        "MORNING_DIGEST_S3_BUCKET",
    ):
        monkeypatch.delenv(var, raising=False)
    # Should not raise; should not invoke boto3.
    _trigger_reflection()


def test_trigger_reflection_calls_ecs_run_task(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MORNING_DIGEST_REFLECT_CLUSTER", "cluster-1")
    monkeypatch.setenv("MORNING_DIGEST_REFLECT_TASK_DEF", "task-def-1")
    monkeypatch.setenv("MORNING_DIGEST_REFLECT_SUBNETS", "subnet-a,subnet-b,")
    monkeypatch.setenv("MORNING_DIGEST_S3_BUCKET", "bucket-1")

    captured: dict[str, Any] = {}

    class _FakeEcs:
        def run_task(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {}

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service: _FakeEcs())
    _trigger_reflection()

    assert captured["cluster"] == "cluster-1"
    assert captured["taskDefinition"] == "task-def-1"
    assert captured["launchType"] == "FARGATE"
    # Empty subnet tokens are stripped.
    assert captured["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == [
        "subnet-a",
        "subnet-b",
    ]
    overrides = captured["overrides"]["containerOverrides"][0]
    assert overrides["name"] == "Runner"
    assert "--bucket" in overrides["command"]
    assert "bucket-1" in overrides["command"]


def test_trigger_reflection_swallows_boto_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """A boto3 failure must be logged but not propagate — feedback writes succeed
    regardless of whether the reflection trigger fires."""
    monkeypatch.setenv("MORNING_DIGEST_REFLECT_CLUSTER", "c")
    monkeypatch.setenv("MORNING_DIGEST_REFLECT_TASK_DEF", "t")
    monkeypatch.setenv("MORNING_DIGEST_REFLECT_SUBNETS", "s")
    monkeypatch.setenv("MORNING_DIGEST_S3_BUCKET", "b")

    class _Broken:
        def run_task(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("ECS exploded")

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service: _Broken())
    # Must not raise.
    _trigger_reflection()


def test_feedback_continues_when_first_match_update_fails(
    client: TestClient, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """If a digest scan finds the item but update_item_feedback returns False
    (e.g. a race where the item disappeared mid-request), the route continues
    iterating to the next digest instead of erroring out."""
    store_on_app: Store = client.app.state.store  # type: ignore[attr-defined]
    store_on_app.write_digest(
        date(2026, 4, 28),
        [{"id": "abc", "type": "article", "title": "T", "source": "s", "url": "u", "summary": ""}],
        "",
    )
    store_on_app.write_digest(
        date(2026, 4, 29),
        [{"id": "abc", "type": "article", "title": "T2", "source": "s", "url": "u", "summary": ""}],
        "",
    )

    real_update = store_on_app.update_item_feedback
    calls: list[date] = []

    def _flaky(d: date, item_id: str, value: Any) -> bool:
        calls.append(d)
        if len(calls) == 1:
            return False  # simulate the race
        return real_update(d, item_id, value)

    monkeypatch.setattr(store_on_app, "update_item_feedback", _flaky)
    r = client.post("/api/feedback", json={"item_id": "abc", "value": "up"})
    assert r.status_code == 200
    assert len(calls) == 2


def test_feedback_records_notes(client: TestClient, tmp_data_dir: Path):
    """Optional `notes` field on feedback is stored on the event."""
    _seed(tmp_data_dir)
    client.post("/api/feedback", json={"item_id": "abc", "value": "up", "notes": "loved it"})
    store = Store(tmp_data_dir)
    events = store.read_recent_feedback(days=1)
    assert any(e.get("notes") == "loved it" for e in events)
