from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from digest.store import Store
from server.app import create_app


def _recent_iso(minutes_ago: int = 0) -> str:
    """ISO8601 timestamp for `minutes_ago` minutes before now (UTC).

    Tests use this instead of hardcoded dates so the 14-day lookback in
    /api/_runs/recent doesn't filter seeded runs out as time passes.
    """
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def client(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MORNING_DIGEST_DATA_DIR", str(tmp_data_dir))
    app = create_app()
    with TestClient(app) as c:
        yield c


def _seed_digest(tmp_data_dir: Path) -> Store:
    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.write_digest(
        date(2026, 4, 29),
        [
            {
                "id": "abc12345",
                "type": "article",
                "title": "Why agents need evals",
                "source": "Example",
                "url": "https://example.com/post-1",
                "summary": "A sober look at LLM agent evals.",
                "feedback": None,
                "run_id": "cur00001",
            },
            {
                "id": "def67890",
                "type": "video",
                "title": "Hand tools 101",
                "source": "Stumpy Nubs",
                "url": "https://youtube.com/watch?v=abc",
                "summary": "Beginner-friendly intro to hand tool woodworking.",
                "duration_min": 12,
                "feedback": None,
                "run_id": "cur00001",
            },
        ],
        agent_notes="Two keepers today.",
    )
    store.append_run(
        {
            "run_id": "cur00001",
            "kind": "curation",
            "started_at": _recent_iso(minutes_ago=120),
            "duration_seconds": 312,
            "tool_calls": 14,
            "tool_log": [],
            "profile_patches": 0,
            "sources_changed": 0,
            "reflection_notes": "",
            "system_prompt": "You are the morning digest curator.",
            "user_prompt": "Curate today's digest.",
            "item_count": 2,
        }
    )
    store.append_run(
        {
            "run_id": "ref00001",
            "kind": "reflection",
            "started_at": _recent_iso(minutes_ago=112),
            "duration_seconds": 18,
            "tool_calls": 4,
            "tool_log": [],
            "profile_patches": 1,
            "sources_changed": 0,
            "reflection_notes": "added woodworking interest per chat feedback.",
            "system_prompt": "You are the morning digest reflector.",
            "user_prompt": "A new feedback event just arrived.",
            "triggering_feedback": {
                "kind": "thumb",
                "value": "up",
                "item_id": "abc12345",
            },
        }
    )
    return store


def test_health(client: TestClient):
    r = client.get("/api/_health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_today_returns_latest(client: TestClient, tmp_data_dir: Path):
    _seed_digest(tmp_data_dir)
    r = client.get("/api/digest/today")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-04-29"
    assert len(body["items"]) == 2


def test_today_returns_null_when_no_digests(client: TestClient):
    r = client.get("/api/digest/today")
    assert r.status_code == 200
    assert r.json() is None


def test_get_by_date(client: TestClient, tmp_data_dir: Path):
    _seed_digest(tmp_data_dir)
    r = client.get("/api/digest/2026-04-29")
    assert r.status_code == 200
    assert r.json()["items"][0]["id"] == "abc12345"

    r = client.get("/api/digest/2026-01-01")
    assert r.status_code == 404


def test_list_digests_only_includes_reacted(client: TestClient, tmp_data_dir: Path):
    """The archive index shows only digests that have at least one reacted item,
    and item_count reflects the reacted count (not the total)."""
    _seed_digest(tmp_data_dir)
    # No reactions yet → archive should be empty.
    r = client.get("/api/digests")
    assert r.status_code == 200
    assert r.json() == []

    # React to one item; that digest now appears with item_count=1.
    client.post("/api/feedback", json={"item_id": "abc12345", "value": "up"})
    r = client.get("/api/digests")
    body = r.json()
    assert len(body) == 1
    assert body[0]["date"] == "2026-04-29"
    assert body[0]["item_count"] == 1


def _seed_two_digests(tmp_data_dir: Path) -> Store:
    """Seed two digests on different dates, both with two unreacted items."""
    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.write_digest(
        date(2026, 4, 28),
        [
            {
                "id": "old-1",
                "type": "article",
                "title": "Older article one",
                "source": "Example",
                "url": "https://example.com/old-1",
                "summary": "From yesterday.",
                "feedback": None,
                "run_id": "cur00428",
            },
            {
                "id": "old-2",
                "type": "article",
                "title": "Older article two",
                "source": "Example",
                "url": "https://example.com/old-2",
                "summary": "From yesterday.",
                "feedback": None,
                "run_id": "cur00428",
            },
        ],
        agent_notes="",
    )
    store.append_run(
        {
            "run_id": "cur00428",
            "kind": "curation",
            "started_at": "2026-04-28T05:00:00+00:00",
            "duration_seconds": 290,
            "tool_calls": 12,
            "tool_log": [],
            "profile_patches": 0,
            "sources_changed": 0,
            "reflection_notes": "",
            "system_prompt": "You are the morning digest curator.",
            "user_prompt": "Curate today's digest.",
            "item_count": 2,
        }
    )
    store.write_digest(
        date(2026, 4, 29),
        [
            {
                "id": "new-1",
                "type": "article",
                "title": "Newer article one",
                "source": "Example",
                "url": "https://example.com/new-1",
                "summary": "From today.",
                "feedback": None,
                "run_id": "cur00429",
            },
            {
                "id": "new-2",
                "type": "article",
                "title": "Newer article two",
                "source": "Example",
                "url": "https://example.com/new-2",
                "summary": "From today.",
                "feedback": None,
                "run_id": "cur00429",
            },
        ],
        agent_notes="",
    )
    store.append_run(
        {
            "run_id": "cur00429",
            "kind": "curation",
            "started_at": "2026-04-29T05:00:00+00:00",
            "duration_seconds": 305,
            "tool_calls": 13,
            "tool_log": [],
            "profile_patches": 0,
            "sources_changed": 0,
            "reflection_notes": "",
            "system_prompt": "You are the morning digest curator.",
            "user_prompt": "Curate today's digest.",
            "item_count": 2,
        }
    )
    return store


def test_feed_returns_unreacted_across_digests_newest_first(client: TestClient, tmp_data_dir: Path):
    _seed_two_digests(tmp_data_dir)
    r = client.get("/api/feed")
    assert r.status_code == 200
    items = r.json()
    assert [i["id"] for i in items] == ["new-1", "new-2", "old-1", "old-2"]
    assert all(i["feedback"] is None for i in items)
    assert items[0]["digest_date"] == "2026-04-29"
    assert items[2]["digest_date"] == "2026-04-28"


def test_feed_excludes_reacted_items(client: TestClient, tmp_data_dir: Path):
    _seed_two_digests(tmp_data_dir)
    client.post("/api/feedback", json={"item_id": "new-1", "value": "up"})
    client.post("/api/feedback", json={"item_id": "old-2", "value": "down"})
    r = client.get("/api/feed")
    ids = [i["id"] for i in r.json()]
    assert ids == ["new-2", "old-1"]


def test_feed_empty_when_all_reacted(client: TestClient, tmp_data_dir: Path):
    _seed_digest(tmp_data_dir)
    client.post("/api/feedback", json={"item_id": "abc12345", "value": "up"})
    client.post("/api/feedback", json={"item_id": "def67890", "value": "down"})
    r = client.get("/api/feed")
    assert r.json() == []


def test_digest_by_date_feedback_only_filters(client: TestClient, tmp_data_dir: Path):
    _seed_digest(tmp_data_dir)
    client.post("/api/feedback", json={"item_id": "abc12345", "value": "up"})

    # Default: returns all items.
    r = client.get("/api/digest/2026-04-29")
    assert len(r.json()["items"]) == 2

    # feedback_only: returns only the reacted item.
    r = client.get("/api/digest/2026-04-29", params={"feedback_only": "true"})
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "abc12345"


def test_thumb_feedback_persists(client: TestClient, tmp_data_dir: Path):
    _seed_digest(tmp_data_dir)
    r = client.post("/api/feedback", json={"item_id": "abc12345", "value": "up"})
    assert r.status_code == 200

    r = client.get("/api/digest/2026-04-29")
    items = r.json()["items"]
    target = next(i for i in items if i["id"] == "abc12345")
    assert target["feedback"] == "up"


def test_thumb_feedback_notes_stored_in_log(client: TestClient, tmp_data_dir: Path):
    _seed_digest(tmp_data_dir)
    r = client.post(
        "/api/feedback", json={"item_id": "abc12345", "value": "up", "notes": "great piece"}
    )
    assert r.status_code == 200
    store = Store(tmp_data_dir)
    events = store.read_recent_feedback(days=1)
    thumb_events = [
        e for e in events if e.get("kind") == "thumb" and e.get("item_id") == "abc12345"
    ]
    assert any(e.get("notes") == "great piece" for e in thumb_events)


def test_thumb_feedback_unknown_item_404(client: TestClient, tmp_data_dir: Path):
    _seed_digest(tmp_data_dir)
    r = client.post("/api/feedback", json={"item_id": "missing", "value": "up"})
    assert r.status_code == 404


def test_search_finds_by_title(client: TestClient, tmp_data_dir: Path):
    _seed_digest(tmp_data_dir)
    r = client.get("/api/search", params={"q": "woodworking"})
    assert r.status_code == 200
    hits = r.json()
    assert len(hits) == 1
    assert hits[0]["title"] == "Hand tools 101"


def test_search_empty_query_returns_empty(client: TestClient):
    r = client.get("/api/search", params={"q": "  "})
    assert r.status_code == 200
    assert r.json() == []


def test_recent_runs(client: TestClient, tmp_data_dir: Path):
    store = _seed_digest(tmp_data_dir)
    store.append_run(
        {
            "run_id": "aaa00001",
            "kind": "curation",
            "started_at": _recent_iso(minutes_ago=60),
            "duration_seconds": 300,
            "item_count": 0,
        }
    )
    store.append_run(
        {
            "run_id": "aaa00002",
            "kind": "reflection",
            "started_at": _recent_iso(minutes_ago=30),
            "duration_seconds": 12,
            "triggering_feedback": {"kind": "thumb", "value": "up", "item_id": "x"},
        }
    )
    r = client.get("/api/_runs/recent")
    assert r.status_code == 200
    body = r.json()
    # Two from _seed_digest (curation + reflection) plus two appended here.
    assert len(body) == 4
    kinds = sorted(e["kind"] for e in body)
    assert kinds == ["curation", "curation", "reflection", "reflection"]


def test_runs_lists_kind_and_triggering_feedback(client: TestClient, tmp_data_dir: Path):
    """/api/runs surfaces the kind field and reflection's triggering_feedback."""
    _seed_digest(tmp_data_dir)
    store = Store(tmp_data_dir)
    store.append_run(
        {
            "run_id": "ref99999",
            "kind": "reflection",
            "started_at": "2026-04-29T08:00:00+00:00",
            "duration_seconds": 20,
            "triggering_feedback": {"kind": "thumb", "value": "down", "item_id": "abc"},
            "profile_patches": 1,
        }
    )
    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.json()
    found = next((row for row in body if row["run_id"] == "ref99999"), None)
    assert found is not None
    assert found["kind"] == "reflection"
    assert found["triggering_feedback"] == {
        "kind": "thumb",
        "value": "down",
        "item_id": "abc",
    }
    assert found["profile_patches"] == 1


def test_runs_surfaces_failed_reflection_with_error(client: TestClient, tmp_data_dir: Path):
    """Failed reflections must show up in /api/runs with exit_reason=error and the error
    message — otherwise they're silently invisible (the bug the user originally hit)."""
    _seed_digest(tmp_data_dir)
    store = Store(tmp_data_dir)
    store.append_run(
        {
            "run_id": "reffail1",
            "kind": "reflection",
            "started_at": "2026-04-29T09:00:00+00:00",
            "duration_seconds": 4,
            "tool_calls": 0,
            "tool_log": [],
            "profile_patches": 0,
            "sources_changed": 0,
            "reflection_notes": "",
            "system_prompt": "...",
            "user_prompt": "...",
            "triggering_feedback": {"kind": "thumb", "value": "up", "item_id": "abc"},
            "exit_reason": "error",
            "error": "RuntimeError: Command failed with exit code 1",
        }
    )

    r = client.get("/api/runs")
    assert r.status_code == 200
    found = next((row for row in r.json() if row["run_id"] == "reffail1"), None)
    assert found is not None
    assert found["kind"] == "reflection"
    assert found["exit_reason"] == "error"
    assert "exit code 1" in found["error"]

    detail = client.get("/api/runs/reffail1")
    assert detail.status_code == 200
    assert detail.json()["exit_reason"] == "error"
    assert "exit code 1" in detail.json()["error"]


def test_runs_legacy_embedded_in_digest_still_visible(client: TestClient, tmp_data_dir: Path):
    """Older digests had runs[] embedded; /api/runs must still surface them."""
    store = _seed_digest(tmp_data_dir)
    data = store.read_digest(date(2026, 4, 29)) or {}
    data["runs"] = [
        {
            "run_id": "legacy01",
            "started_at": "2026-04-29T05:00:00+00:00",
            "duration_seconds": 100,
            "tool_calls": 5,
            "profile_patches": 0,
            "sources_changed": 0,
            "reflection_notes": "ok",
        }
    ]
    store.rewrite_digest(date(2026, 4, 29), data)
    r = client.get("/api/runs")
    assert r.status_code == 200
    ids = [row["run_id"] for row in r.json()]
    assert "legacy01" in ids
    legacy = next(row for row in r.json() if row["run_id"] == "legacy01")
    assert legacy["kind"] == "curation"
