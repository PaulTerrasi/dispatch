"""S3Store round-trip tests using moto.

The S3Store interface mirrors digest.store.Store; these tests assert behavioral
parity for the operations actually used at runtime, plus the cache-invalidation
hot path that gets bitten in production when concurrent writers land.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws

from digest.s3_store import S3Store
from digest.store import Source

BUCKET = "test-morning-digest"


@pytest.fixture
def store() -> Any:
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield S3Store(BUCKET)


def test_ensure_layout_seeds_defaults(store: S3Store) -> None:
    store.ensure_layout()
    assert "Profile" in store.read_profile()
    assert store.list_sources() == []  # default empty sources file
    assert store.is_profile_empty() is True


def test_profile_round_trip(store: S3Store) -> None:
    store.ensure_layout()
    store.write_profile("# Real profile\n\n## Standing interests\n- ML\n")
    assert "ML" in store.read_profile()
    assert store.is_profile_empty() is False


def test_digest_write_then_read(store: S3Store) -> None:
    store.ensure_layout()
    today = date(2026, 5, 9)
    items: list[dict[str, Any]] = [
        {"id": "a", "type": "article", "title": "T1", "source": "s", "url": "u", "summary": "x"},
    ]
    store.write_digest(today, items, agent_notes="ok")
    data = store.read_digest(today)
    assert data is not None
    assert [i["id"] for i in data["items"]] == ["a"]
    assert data["agent_notes"] == "ok"


def test_digest_write_dedupes_by_id(store: S3Store) -> None:
    """Re-running a digest on the same day must not duplicate items by id."""
    store.ensure_layout()
    today = date(2026, 5, 9)
    item = {"id": "a", "type": "article", "title": "T1", "source": "s", "url": "u", "summary": ""}
    store.write_digest(today, [item], agent_notes="first")
    store.write_digest(today, [item, {**item, "id": "b"}], agent_notes="second")
    data = store.read_digest(today)
    assert data is not None
    assert sorted(i["id"] for i in data["items"]) == ["a", "b"]
    assert data["agent_notes"] == "second"


def test_rewrite_digest_replaces_payload(store: S3Store) -> None:
    store.ensure_layout()
    today = date(2026, 5, 9)
    store.write_digest(today, [{"id": "a"}], agent_notes="")
    store.rewrite_digest(today, {"date": today.isoformat(), "items": [], "agent_notes": "new"})
    data = store.read_digest(today)
    assert data == {"date": today.isoformat(), "items": [], "agent_notes": "new"}


def test_list_digests_returns_sorted_dates(store: S3Store) -> None:
    store.ensure_layout()
    for d in (date(2026, 5, 1), date(2026, 5, 9), date(2026, 5, 5)):
        store.write_digest(d, [], "")
    assert store.list_digests() == [date(2026, 5, 9), date(2026, 5, 5), date(2026, 5, 1)]


def test_cache_invalidates_on_write(store: S3Store) -> None:
    """Regression: stale cache + concurrent writers caused last-write-wins drift.
    The cache must be cleared by every mutation so subsequent reads see the new state.
    """
    store.ensure_layout()
    today = date(2026, 5, 9)
    store.write_digest(today, [{"id": "a"}], agent_notes="")

    # Prime the cache.
    assert store.read_digest(today) is not None

    # Out-of-band write directly through boto3 (simulates a concurrent writer).
    store.s3.put_object(
        Bucket=BUCKET,
        Key=f"digests/{today.isoformat()}.json",
        Body=b'{"date": "2026-05-09", "items": [], "agent_notes": "out-of-band"}',
    )

    # Mutating via the store should clear the cache so the next read is fresh.
    store.rewrite_digest(today, {"date": today.isoformat(), "items": [], "agent_notes": "after"})
    data = store.read_digest(today)
    assert data is not None
    assert data["agent_notes"] == "after"


def test_feedback_append_then_read(store: S3Store) -> None:
    store.ensure_layout()
    store.append_feedback({"kind": "thumb", "value": "up", "item_id": "a"})
    store.append_feedback({"kind": "chat", "text": "more like this"})
    events = store.read_recent_feedback(days=30)
    kinds = sorted(e["kind"] for e in events)
    assert kinds == ["chat", "thumb"]
    # Each event gets a ts stamped on append.
    assert all("ts" in e for e in events)


def test_sources_round_trip(store: S3Store) -> None:
    store.ensure_layout()
    sources = [
        Source(kind="rss", value="https://example.com/feed", tags=["tech"]),
        Source(kind="youtube", value="UC123", name="Channel", tags=[]),
        Source(kind="site", value="https://blog.example.com"),
    ]
    store.write_sources(sources)
    out = store.list_sources()
    assert {(s.kind, s.value) for s in out} == {(s.kind, s.value) for s in sources}


def test_update_item_feedback_flips_value(store: S3Store) -> None:
    store.ensure_layout()
    today = date(2026, 5, 9)
    item = {"id": "a", "type": "article", "title": "T", "source": "s", "url": "u", "summary": ""}
    store.write_digest(today, [item], agent_notes="")
    assert store.update_item_feedback(today, "a", "up") is True
    data = store.read_digest(today)
    assert data is not None
    assert data["items"][0]["feedback"] == "up"
    # Unknown id returns False without mutating.
    assert store.update_item_feedback(today, "missing", "up") is False


def test_recent_digest_items_filters_by_window(store: S3Store) -> None:
    store.ensure_layout()
    today = datetime.now(UTC).date()
    inside = today
    item = {"id": "a", "type": "article", "title": "T", "source": "s", "url": "u", "summary": ""}
    store.write_digest(inside, [item], "")
    out = store.recent_digest_items(days=7)
    assert [i["id"] for i in out] == ["a"]


def test_runs_round_trip(store: S3Store) -> None:
    today = datetime.now(UTC).date().isoformat()
    store.append_run({"run_id": "r1", "kind": "curation", "started_at": f"{today}T10:00:00+00:00"})
    store.append_run(
        {
            "run_id": "r2",
            "kind": "reflection",
            "started_at": f"{today}T11:00:00+00:00",
            "triggering_feedback": {"kind": "thumb", "value": "up"},
        }
    )
    runs = store.read_recent_runs(days=1)
    assert [r["run_id"] for r in runs] == ["r2", "r1"]
    assert store.read_run("r1") is not None
    assert store.read_run("missing") is None


@pytest.mark.skip(
    reason="moto does not implement S3 IfNoneMatch conditional puts; "
    "lock semantics are exercised by the filesystem Store tests."
)
def test_reflection_lock_round_trip(store: S3Store) -> None:
    token = store.try_acquire_reflection_lock(ttl_seconds=900)
    assert token is not None
    assert store.try_acquire_reflection_lock(ttl_seconds=900) is None
    store.release_reflection_lock(token)
    assert store.try_acquire_reflection_lock(ttl_seconds=900) is not None


def test_reflection_cursor_round_trip(store: S3Store) -> None:
    assert store.read_reflection_cursor() is None
    store.write_reflection_cursor("2026-05-09T12:00:00+00:00")
    assert store.read_reflection_cursor() == "2026-05-09T12:00:00+00:00"


def test_reflection_memory_round_trip(store: S3Store) -> None:
    store.ensure_layout()
    assert "Reflection memory" in store.read_reflection_memory()
    store.write_reflection_memory("# Notes\n- watch source X\n")
    assert "watch source X" in store.read_reflection_memory()


def test_git_methods_are_noops(store: S3Store) -> None:
    """S3 versioning replaces git; the methods exist to satisfy the protocol."""
    store.git_init_if_needed()  # should not raise
    assert store.git_commit_all("anything") in (False, None)
