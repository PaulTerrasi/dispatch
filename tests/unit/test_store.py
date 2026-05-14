from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from digest.store import Source, Store


def test_ensure_layout_creates_seed_files(tmp_data_dir):
    store = Store(tmp_data_dir)
    store.ensure_layout()
    assert store.profile_path.exists()
    assert store.sources_path.exists()
    assert store.digests_dir.is_dir()
    assert store.feedback_dir.is_dir()


def test_list_sources_parses_yaml(store: Store):
    store.sources_path.write_text(
        "rss:\n"
        "  - url: https://example.com/feed\n"
        "    tags: [llm, news]\n"
        "youtube:\n"
        "  - channel_id: UC1234\n"
        "    name: Example\n"
        "    tags: [woodworking]\n"
        "sites:\n"
        "  - https://news.ycombinator.com\n",
        encoding="utf-8",
    )
    sources = store.list_sources()
    kinds = sorted({s.kind for s in sources})
    assert kinds == ["rss", "site", "youtube"]
    rss = next(s for s in sources if s.kind == "rss")
    assert rss == Source(kind="rss", value="https://example.com/feed", tags=["llm", "news"])
    yt = next(s for s in sources if s.kind == "youtube")
    assert yt.value == "UC1234"
    assert yt.name == "Example"


def test_write_and_read_digest_roundtrip(store: Store):
    d = date(2026, 4, 29)
    items = [
        {
            "id": "abc12345",
            "type": "article",
            "title": "Hello",
            "source": "Example",
            "url": "https://example.com/a",
            "summary": "...",
            "feedback": None,
        }
    ]
    store.write_digest(d, items, agent_notes="quiet morning")
    got = store.read_digest(d)
    assert got is not None
    assert got["date"] == "2026-04-29"
    assert got["items"] == items
    assert got["agent_notes"] == "quiet morning"


def test_recent_digest_items_filters_by_window(store: Store):
    today = datetime.now(UTC).date()
    old = today - timedelta(days=10)
    fresh = today - timedelta(days=2)
    store.write_digest(old, [{"id": "1", "title": "Old", "url": "u1", "source": "old.com"}], "")
    store.write_digest(
        fresh, [{"id": "2", "title": "Fresh", "url": "u2", "source": "fresh.com"}], ""
    )

    recent = store.recent_digest_items(days=7)
    titles = {r["title"] for r in recent}
    assert titles == {"Fresh"}
    assert recent[0]["source"] == "fresh.com"
    assert recent[0]["id"] == "2"


def test_write_sources_roundtrip(store: Store):
    sources = [
        Source(kind="rss", value="https://example.com/feed", tags=["llm", "news"]),
        Source(kind="youtube", value="UC1234", name="Example", tags=["woodworking"]),
        Source(kind="site", value="https://news.ycombinator.com"),
    ]
    store.write_sources(sources)
    roundtripped = store.list_sources()
    assert len(roundtripped) == 3
    rss = next(s for s in roundtripped if s.kind == "rss")
    assert rss.value == "https://example.com/feed"
    assert rss.tags == ["llm", "news"]
    yt = next(s for s in roundtripped if s.kind == "youtube")
    assert yt.value == "UC1234"
    assert yt.name == "Example"
    assert yt.tags == ["woodworking"]
    site = next(s for s in roundtripped if s.kind == "site")
    assert site.value == "https://news.ycombinator.com"


def test_write_sources_empty(store: Store):
    store.write_sources([])
    assert store.list_sources() == []


def test_write_sources_preserves_header(store: Store):
    store.write_sources([])
    text = store.sources_path.read_text(encoding="utf-8")
    assert text.startswith("# Sources for the daily digest")


def test_update_item_feedback_persists(store: Store):
    d = date(2026, 4, 29)
    store.write_digest(d, [{"id": "x", "title": "T", "url": "U", "feedback": None}], "")
    assert store.update_item_feedback(d, "x", "up") is True
    again = store.read_digest(d)
    assert again["items"][0]["feedback"] == "up"


def test_update_item_feedback_missing_returns_false(store: Store):
    d = date(2026, 4, 29)
    store.write_digest(d, [{"id": "x", "title": "T", "url": "U"}], "")
    assert store.update_item_feedback(d, "missing", "up") is False


def test_append_and_read_recent_feedback(store: Store):
    store.append_feedback({"item_id": "a1", "kind": "thumb", "value": "up"})
    store.append_feedback({"kind": "chat", "text": "more woodworking"})
    events = store.read_recent_feedback(days=1)
    kinds = {e["kind"] for e in events}
    assert kinds == {"thumb", "chat"}


def test_is_profile_empty_seed(store: Store):
    """The default seed file (all bare `-` bullets) counts as empty."""
    assert store.is_profile_empty() is True


def test_is_profile_empty_real_content(store: Store):
    store.write_profile("# Profile\n\n## Standing interests\n- LLM agents and tool use\n")
    assert store.is_profile_empty() is False


def test_is_profile_empty_only_headings(store: Store):
    store.write_profile("# Profile\n\n## Standing interests\n## Voice notes\n")
    assert store.is_profile_empty() is True


def test_git_init_and_commit(store: Store):
    store.git_init_if_needed()
    store.write_profile("# Profile\n\n## Standing interests\n- LLMs\n")
    assert store.git_commit_all("init") is True
    # Second call with no changes returns False
    assert store.git_commit_all("noop") is False


def test_append_and_read_recent_runs(store: Store):
    from datetime import UTC, datetime

    store.ensure_layout()
    today = datetime.now(UTC).date().isoformat()
    store.append_run(
        {
            "run_id": "abc1",
            "kind": "curation",
            "started_at": f"{today}T10:00:00+00:00",
            "item_count": 3,
        }
    )
    store.append_run(
        {
            "run_id": "abc2",
            "kind": "reflection",
            "started_at": f"{today}T11:00:00+00:00",
            "triggering_feedback": {"kind": "thumb", "value": "up", "item_id": "x"},
        }
    )
    runs = store.read_recent_runs(days=1)
    # Newest first.
    assert [r["run_id"] for r in runs] == ["abc2", "abc1"]
    assert runs[0]["kind"] == "reflection"
    assert runs[1]["kind"] == "curation"


def test_read_run_finds_by_id(store: Store):
    from datetime import UTC, datetime

    store.ensure_layout()
    today = datetime.now(UTC).date().isoformat()
    store.append_run(
        {"run_id": "find_me", "kind": "curation", "started_at": f"{today}T10:00:00+00:00"}
    )
    found = store.read_run("find_me")
    assert found is not None
    assert found["kind"] == "curation"
    assert store.read_run("missing") is None


def test_reflection_lock_acquire_blocks_second_caller(store: Store):
    store.ensure_layout()
    token1 = store.try_acquire_reflection_lock(ttl_seconds=900)
    assert token1 is not None
    token2 = store.try_acquire_reflection_lock(ttl_seconds=900)
    assert token2 is None  # already held
    store.release_reflection_lock(token1)
    token3 = store.try_acquire_reflection_lock(ttl_seconds=900)
    assert token3 is not None


def test_reflection_lock_stale_can_be_stolen(store: Store):
    store.ensure_layout()
    # Manually plant an expired lock.
    import json as _json

    store._lock_path.write_text(
        _json.dumps(
            {
                "token": "old",
                "started_at": "2020-01-01T00:00:00+00:00",
                "expires_at": "2020-01-01T00:15:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    token = store.try_acquire_reflection_lock(ttl_seconds=900)
    assert token is not None
    assert token != "old"


def test_reflection_lock_release_only_with_matching_token(store: Store):
    store.ensure_layout()
    token = store.try_acquire_reflection_lock(ttl_seconds=900)
    assert token is not None
    store.release_reflection_lock("wrong_token")  # no-op
    # Should still be held.
    assert store.try_acquire_reflection_lock(ttl_seconds=900) is None
    store.release_reflection_lock(token)
    assert store.try_acquire_reflection_lock(ttl_seconds=900) is not None


def test_reflection_cursor_round_trip(store: Store):
    store.ensure_layout()
    assert store.read_reflection_cursor() is None
    store.write_reflection_cursor("2026-04-29T10:00:00+00:00")
    assert store.read_reflection_cursor() == "2026-04-29T10:00:00+00:00"


def test_reflection_memory_round_trip(store: Store):
    store.ensure_layout()
    # ensure_layout seeds a default
    assert "Reflection memory" in store.read_reflection_memory()
    store.write_reflection_memory("# Notes\n- watching political-hot-take pattern\n")
    assert "watching political-hot-take" in store.read_reflection_memory()


def test_reflection_memory_default_when_missing(tmp_data_dir):
    """read_reflection_memory returns the default seed even before ensure_layout."""
    store = Store(tmp_data_dir)
    text = store.read_reflection_memory()
    assert "No notes yet" in text
