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


def test_get_run_returns_detail_for_known_run_id(client: TestClient, tmp_data_dir: Path):
    """GET /runs/{run_id} returns the full record (incl. tool_log) for a stored run."""
    _seed_digest(tmp_data_dir)
    r = client.get("/api/runs/cur00001")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "cur00001"
    assert body["kind"] == "curation"
    assert body["system_prompt"]
    assert isinstance(body["tool_log"], list)


def test_get_run_404_for_unknown(client: TestClient):
    r = client.get("/api/runs/does-not-exist")
    assert r.status_code == 404


def test_get_run_falls_back_to_legacy_embedded_run(client: TestClient, tmp_data_dir: Path):
    """A run_id that lives only inside a legacy digest['runs'] (no /runs/ entry)
    must still be retrievable via /api/runs/{run_id}."""
    store = _seed_digest(tmp_data_dir)
    data = store.read_digest(date(2026, 4, 29)) or {}
    data["runs"] = [
        {
            "run_id": "legacy42",
            "started_at": "2026-04-29T05:00:00+00:00",
            "duration_seconds": 99,
            "tool_calls": 7,
            "tool_log": [
                {
                    "ts": "2026-04-29T05:00:01+00:00",
                    "tool": "read_profile",
                    "args": {},
                    "outcome": "ok",
                    # Legacy flat profile_snapshot → mapped to details by the API.
                    "profile_snapshot": "# legacy profile\n",
                },
                "garbage-entry-not-a-dict",  # exercises the malformed tool_log branch
            ],
            "profile_patches": 0,
            "sources_changed": 0,
            "reflection_notes": "ok",
            "curation_system_prompt": "you are X",
            "curation_user_prompt": "do Y",
        }
    ]
    # Add an item without a run_id to take the `else len(digest_items)` path.
    data["items"].append(
        {
            "id": "stray",
            "type": "article",
            "title": "Stray",
            "source": "s",
            "url": "u",
            "summary": "",
            # NB: missing run_id → forces the counts-empty branch
        }
    )
    # And one truly-malformed item (missing required fields) to exercise
    # the DigestItem TypeError-handler.
    data["items"].append({"id": "broken"})
    store.rewrite_digest(date(2026, 4, 29), data)

    r = client.get("/api/runs/legacy42")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "legacy42"
    assert body["kind"] == "curation"
    assert body["agent_notes"]  # carried over from the digest
    # Malformed tool-log entry was logged & skipped; the good one remains.
    assert len(body["tool_log"]) == 1
    # Legacy flat profile_snapshot was hoisted into the new details field.
    assert body["tool_log"][0]["details"] == {"profile_snapshot": "# legacy profile\n"}


def test_get_run_falls_back_to_legacy_singular_run_key(client: TestClient, tmp_data_dir: Path):
    """Even older digests stored a single `run` dict instead of a `runs` list.
    `_runs_from_digest` must handle both."""
    store = _seed_digest(tmp_data_dir)
    data = store.read_digest(date(2026, 4, 29)) or {}
    data.pop("runs", None)
    data["run"] = {
        "run_id": "veryold",
        "started_at": "2026-04-29T05:00:00+00:00",
        "duration_seconds": 42,
    }
    store.rewrite_digest(date(2026, 4, 29), data)

    r = client.get("/api/runs/veryold")
    assert r.status_code == 200
    assert r.json()["run_id"] == "veryold"

    # _all_runs also surfaces it.
    r2 = client.get("/api/runs")
    assert any(row["run_id"] == "veryold" for row in r2.json())


def test_get_by_date_rejects_bad_date(client: TestClient):
    r = client.get("/api/digest/not-a-date")
    assert r.status_code == 400


def test_today_returns_none_when_digest_file_unreadable(client: TestClient, tmp_data_dir: Path):
    """If list_digests reports a date but read_digest returns None (e.g. the
    file was truncated mid-write), `/digest/today` answers null instead of crashing."""
    today_date = date(2026, 5, 14)
    store = Store(tmp_data_dir)
    store.ensure_layout()
    # Plant a digest file that list_digests will see, then overwrite it with
    # invalid JSON via direct file write — read_digest will crash on bad JSON,
    # so instead we make read_digest return None by stubbing the store on the app.
    store.write_digest(today_date, [], "")
    # Use the underlying Store on app.state.
    real_read = client.app.state.store.read_digest  # type: ignore[attr-defined]
    client.app.state.store.read_digest = lambda _d: None  # type: ignore[assignment]
    try:
        r = client.get("/api/digest/today")
        assert r.status_code == 200
        assert r.json() is None
    finally:
        client.app.state.store.read_digest = real_read  # type: ignore[assignment]


def test_runs_handles_digest_items_without_run_id(client: TestClient, tmp_data_dir: Path):
    """In `_all_runs`, when iterating legacy digests, items missing run_id
    must not crash the per-run counts tracking."""
    store = _seed_digest(tmp_data_dir)
    data = store.read_digest(date(2026, 4, 29)) or {}
    # Add an item without run_id.
    data["items"].append(
        {
            "id": "no_rid",
            "type": "article",
            "title": "Stray",
            "source": "s",
            "url": "u",
            "summary": "",
        }
    )
    store.rewrite_digest(date(2026, 4, 29), data)
    r = client.get("/api/runs")
    assert r.status_code == 200


def test_get_run_legacy_match_search_iterates_multiple_digests(
    client: TestClient, tmp_data_dir: Path
):
    """When the target run_id lives in the SECOND digest, the iterator must
    skip past the first (non-matching) digest cleanly."""
    store = _seed_digest(tmp_data_dir)
    # The seeded digest at 2026-04-29 has no legacy `runs` key.
    # Add a SECOND digest WITH a legacy run.
    store.write_digest(
        date(2026, 4, 28),
        [{"id": "z", "type": "article", "title": "Z", "source": "s", "url": "u", "summary": ""}],
        "",
    )
    data = store.read_digest(date(2026, 4, 28)) or {}
    data["runs"] = [
        {
            "run_id": "wrong_id_1",
            "started_at": "2026-04-28T05:00:00+00:00",
            "duration_seconds": 1,
        },
        {
            "run_id": "wanted",
            "started_at": "2026-04-28T06:00:00+00:00",
            "duration_seconds": 2,
        },
    ]
    store.rewrite_digest(date(2026, 4, 28), data)

    r = client.get("/api/runs/wanted")
    assert r.status_code == 200
    assert r.json()["run_id"] == "wanted"


def test_runs_handles_record_without_run_id(client: TestClient, tmp_data_dir: Path):
    """A persisted run record missing `run_id` (e.g. old schema) should still
    surface in /api/runs without crashing — it just can't be deduped against
    legacy embedded runs."""
    store = _seed_digest(tmp_data_dir)
    today = date(2026, 4, 29).isoformat()
    store.append_run(
        {
            # no run_id
            "kind": "curation",
            "started_at": f"{today}T08:00:00+00:00",
            "duration_seconds": 5,
        }
    )
    r = client.get("/api/runs")
    assert r.status_code == 200
    # The result list survives the missing run_id.
    assert any(row["run_id"] is None for row in r.json())


def test_runs_handles_legacy_run_with_no_run_id(client: TestClient, tmp_data_dir: Path):
    """Legacy embedded runs missing run_id must not crash the seen-set tracking."""
    store = _seed_digest(tmp_data_dir)
    data = store.read_digest(date(2026, 4, 29)) or {}
    data["runs"] = [
        {
            # no run_id
            "started_at": "2026-04-29T04:00:00+00:00",
            "duration_seconds": 9,
        }
    ]
    store.rewrite_digest(date(2026, 4, 29), data)
    r = client.get("/api/runs")
    assert r.status_code == 200


def test_run_date_uses_fallback_when_started_at_is_short(client: TestClient, tmp_data_dir: Path):
    """`_run_date` falls back to the digest date when `started_at` is a string
    too short to slice an ISO date out of (length < 10)."""
    store = _seed_digest(tmp_data_dir)
    data = store.read_digest(date(2026, 4, 29)) or {}
    data["runs"] = [
        {
            "run_id": "short_ts",
            "started_at": "short",  # < 10 chars → falls back to digest date
            "duration_seconds": 1,
        }
    ]
    store.rewrite_digest(date(2026, 4, 29), data)
    r = client.get("/api/runs")
    short = next((row for row in r.json() if row["run_id"] == "short_ts"), None)
    assert short is not None
    # Fallback returns the digest date.
    assert short["date"] == "2026-04-29"


def test_run_date_falls_back_to_prefix_when_started_at_unparseable(
    client: TestClient, tmp_data_dir: Path
):
    """`_run_date` tolerates a corrupt `started_at` that is long enough to slice
    but not ISO-parseable (e.g. legacy junk): the app-timezone conversion raises
    ValueError, so it returns the raw 10-char prefix instead of 500-ing."""
    store = _seed_digest(tmp_data_dir)
    data = store.read_digest(date(2026, 4, 29)) or {}
    data["runs"] = [
        {
            "run_id": "bad_ts",
            "started_at": "not-a-date-string",  # len >= 10 but not ISO → except branch
            "duration_seconds": 1,
        }
    ]
    store.rewrite_digest(date(2026, 4, 29), data)
    r = client.get("/api/runs")
    bad = next((row for row in r.json() if row["run_id"] == "bad_ts"), None)
    assert bad is not None
    assert bad["date"] == "not-a-date"  # ts[:10]


def test_runs_excludes_duplicate_when_present_in_both_stores(
    client: TestClient, tmp_data_dir: Path
):
    """Same run_id in both new-style runs/ and legacy digest['runs'] is dedup'd
    in favor of the new-store record."""
    store = _seed_digest(tmp_data_dir)
    # cur00001 already exists in runs/. Now embed it in digest['runs'] too.
    data = store.read_digest(date(2026, 4, 29)) or {}
    data["runs"] = [
        {
            "run_id": "cur00001",
            "started_at": "2026-04-29T04:00:00+00:00",
            "duration_seconds": 1,
            "reflection_notes": "legacy_should_not_win",
        }
    ]
    store.rewrite_digest(date(2026, 4, 29), data)
    r = client.get("/api/runs")
    rows = [row for row in r.json() if row["run_id"] == "cur00001"]
    assert len(rows) == 1
    # The new-store record (no `legacy_should_not_win` reflection_notes) wins.
    assert "legacy_should_not_win" not in rows[0].get("reflection_notes", "")
