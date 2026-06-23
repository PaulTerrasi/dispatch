"""Tests for /api/feedback and /api/chat — including the `_schedule_reflection`
debounce, which creates a one-time EventBridge schedule so we mock boto3 to
assert behavior.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from digest.store import Store
from server.app import create_app
from server.routes_feedback import _schedule_reflection


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
    """If `read_digest` returns None for one of the dates the loop must continue
    on to the next digest — this is the `if not digest: continue` branch."""
    store_on_app: Store = client.app.state.store  # type: ignore[attr-defined]
    store_on_app.ensure_layout()
    # Two digests: list_digests returns newest first. We make the newest read
    # return None to drive the `continue`; the older digest has the item.
    store_on_app.write_digest(date(2026, 4, 29), [], "")  # newest, will read as None
    store_on_app.write_digest(
        date(2026, 4, 28),
        [{"id": "abc", "type": "article", "title": "T", "source": "s", "url": "u", "summary": ""}],
        "",
    )
    real_read = store_on_app.read_digest

    def _none_then_real(d: date) -> Any:
        # Only the newest date is masked as None; the older digest reads normally.
        if d == date(2026, 4, 29):
            return None
        return real_read(d)

    monkeypatch.setattr(store_on_app, "read_digest", _none_then_real)
    r = client.post("/api/feedback", json={"item_id": "abc", "value": "up"})
    assert r.status_code == 200


def test_feedback_value_none_clears_existing(client: TestClient, tmp_data_dir: Path):
    """`value: "none"` translates into a stored feedback of `null`."""
    _seed(tmp_data_dir)
    client.post("/api/feedback", json={"item_id": "abc", "value": "up"})
    r = client.post("/api/feedback", json={"item_id": "abc", "value": "none"})
    assert r.status_code == 200
    store = Store(tmp_data_dir)
    item = next(i for i in (store.read_digest(date(2026, 4, 29)) or {})["items"])
    assert item["feedback"] is None


# ── _schedule_reflection ───────────────────────────────────────────────────


_REFLECT_ENV = {
    "MORNING_DIGEST_REFLECT_CLUSTER": "cluster-1",
    "MORNING_DIGEST_REFLECT_TASK_DEF": "task-def-1",
    "MORNING_DIGEST_REFLECT_SUBNETS": "subnet-a,subnet-b,",
    "MORNING_DIGEST_S3_BUCKET": "bucket-1",
    "MORNING_DIGEST_REFLECT_SCHEDULER_ROLE_ARN": "arn:aws:iam::123:role/scheduler",
    "MORNING_DIGEST_REFLECT_SCHEDULE_NAME": "morning-digest-reflect-pending",
}


def test_schedule_reflection_noop_when_env_missing(monkeypatch: pytest.MonkeyPatch):
    """Local dev (no MORNING_DIGEST_REFLECT_* envs) → logs and returns, no boto3."""
    for var in (*_REFLECT_ENV, "MORNING_DIGEST_REFLECT_SCHEDULER_ROLE_ARN"):
        monkeypatch.delenv(var, raising=False)

    import boto3

    def _boom(service: str) -> Any:  # pragma: no cover - must not be reached
        raise AssertionError("boto3 should not be called when env is missing")

    monkeypatch.setattr(boto3, "client", _boom)
    _schedule_reflection()


def test_schedule_reflection_creates_one_time_schedule(monkeypatch: pytest.MonkeyPatch):
    for k, v in _REFLECT_ENV.items():
        monkeypatch.setenv(k, v)

    captured: dict[str, Any] = {}

    class _FakeScheduler:
        def create_schedule(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {}

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service: _FakeScheduler())
    _schedule_reflection()

    assert captured["Name"] == "morning-digest-reflect-pending"
    assert captured["ScheduleExpression"].startswith("at(")
    assert captured["ActionAfterCompletion"] == "DELETE"
    assert captured["Target"]["RoleArn"] == "arn:aws:iam::123:role/scheduler"
    target_input = json.loads(captured["Target"]["Input"])
    assert target_input["Cluster"] == "cluster-1"
    assert target_input["TaskDefinition"] == "task-def-1"
    # Empty subnet tokens are stripped.
    assert target_input["NetworkConfiguration"]["AwsvpcConfiguration"]["Subnets"] == [
        "subnet-a",
        "subnet-b",
    ]
    command = target_input["Overrides"]["ContainerOverrides"][0]["Command"]
    assert "--reflect-drain" in command
    assert "--bucket" in command
    assert "bucket-1" in command


def test_schedule_reflection_conflict_is_noop(monkeypatch: pytest.MonkeyPatch):
    """A ConflictException means the debounce window is already open — fold this
    feedback into the pending drain instead of erroring."""
    for k, v in _REFLECT_ENV.items():
        monkeypatch.setenv(k, v)

    from botocore.exceptions import ClientError

    class _Conflict:
        def create_schedule(self, **_kwargs: Any) -> dict[str, Any]:
            raise ClientError(
                {"Error": {"Code": "ConflictException", "Message": "exists"}},
                "CreateSchedule",
            )

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service: _Conflict())
    # Must not raise.
    _schedule_reflection()


def test_schedule_reflection_swallows_boto_errors(monkeypatch: pytest.MonkeyPatch):
    """A non-conflict boto3 failure must be logged but not propagate — feedback
    writes succeed regardless of whether the reflection schedule is created."""
    for k, v in _REFLECT_ENV.items():
        monkeypatch.setenv(k, v)

    class _Broken:
        def create_schedule(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("Scheduler exploded")

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service: _Broken())
    # Must not raise.
    _schedule_reflection()


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
