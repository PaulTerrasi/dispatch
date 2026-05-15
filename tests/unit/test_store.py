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


def test_write_sources_youtube_without_name_or_tags(store: Store):
    """A YouTube source with no `name`/`tags` should serialize a bare channel_id entry."""
    store.write_sources([Source(kind="youtube", value="UCminimal")])
    text = store.sources_path.read_text(encoding="utf-8")
    assert "channel_id: UCminimal" in text
    assert "name:" not in text
    # Round-trips back losslessly.
    yt = next(s for s in store.list_sources() if s.kind == "youtube")
    assert yt.name is None
    assert yt.tags == []


def test_write_sources_ignores_unknown_kind(store: Store):
    """An unknown source kind is dropped without raising — the YAML stays sane."""
    store.write_sources(
        [
            Source(kind="rss", value="https://ok.example/feed"),
            Source(kind="podcast", value="https://podcasts.example/feed"),  # bogus
        ]
    )
    kinds = {s.kind for s in store.list_sources()}
    assert kinds == {"rss"}


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


# ── small-branch coverage ──────────────────────────────────────────────────


def test_list_sources_when_file_missing(tmp_data_dir):
    """If sources.yaml doesn't exist, list_sources returns an empty list."""
    s = Store(tmp_data_dir)
    assert s.list_sources() == []


def test_list_sources_skips_blank_site_entries(store: Store):
    """A `sites:` entry given as an object without `url`, or with falsy url, is ignored."""
    store.sources_path.write_text(
        "rss: []\nyoutube: []\nsites:\n"
        "  - url: https://example.com\n"
        "  - url: \n"  # falsy
        "  - {}\n",  # dict missing url
        encoding="utf-8",
    )
    sites = [s for s in store.list_sources() if s.kind == "site"]
    assert [s.value for s in sites] == ["https://example.com"]


def test_write_digest_merges_with_existing_dedupes_by_id(store: Store):
    """Second call must add new items, leave existing ids alone, and update agent_notes."""
    d = date(2026, 4, 29)
    store.write_digest(d, [{"id": "a", "title": "A", "url": "u"}], "first")
    store.write_digest(
        d,
        [
            {"id": "a", "title": "A-dup", "url": "u"},  # duplicate id — ignored
            {"id": "b", "title": "B", "url": "v"},
        ],
        "second",
    )
    data = store.read_digest(d)
    assert data is not None
    assert sorted(i["id"] for i in data["items"]) == ["a", "b"]
    # First-write title for "a" wins (we don't overwrite).
    a = next(i for i in data["items"] if i["id"] == "a")
    assert a["title"] == "A"
    assert data["agent_notes"] == "second"


def test_list_digests_missing_dir_returns_empty(tmp_data_dir):
    s = Store(tmp_data_dir)
    # ensure_layout NOT called — digests/ does not exist.
    assert s.list_digests() == []


def test_list_digests_ignores_non_date_filenames(store: Store):
    """Stray junk like `latest.json` shouldn't crash the lister."""
    (store.digests_dir / "not-a-date.json").write_text("{}", encoding="utf-8")
    (store.digests_dir / "2026-04-29.json").write_text("{}", encoding="utf-8")
    assert store.list_digests() == [date(2026, 4, 29)]


def test_recent_digest_items_skips_empty_digest_file(store: Store):
    """A digest with empty body or no `items` key shouldn't break recent_digest_items."""
    d = datetime.now(UTC).date()
    store.digest_path(d).write_text("{}", encoding="utf-8")  # no items
    assert store.recent_digest_items(days=1) == []


def test_update_item_feedback_missing_digest_returns_false(store: Store):
    assert store.update_item_feedback(date(2026, 1, 1), "any", "up") is False


def test_read_recent_feedback_dir_missing(tmp_data_dir):
    s = Store(tmp_data_dir)
    assert s.read_recent_feedback(days=1) == []


def test_read_recent_feedback_skips_bad_filenames_and_old_files(store: Store):
    """Filenames that don't parse as dates and files outside the window are skipped."""
    today = datetime.now(UTC).date()
    old = today - timedelta(days=60)
    (store.feedback_dir / "garbage.jsonl").write_text("{}\n", encoding="utf-8")
    (store.feedback_dir / f"{old.isoformat()}.jsonl").write_text(
        '{"kind":"thumb","ts":"2020-01-01T00:00:00+00:00"}\n', encoding="utf-8"
    )
    (store.feedback_dir / f"{today.isoformat()}.jsonl").write_text(
        '{"kind":"chat","text":"x"}\n', encoding="utf-8"
    )
    out = store.read_recent_feedback(days=14)
    assert {e["kind"] for e in out} == {"chat"}


def test_read_recent_feedback_skips_blank_and_invalid_json_lines(store: Store):
    today = datetime.now(UTC).date()
    (store.feedback_dir / f"{today.isoformat()}.jsonl").write_text(
        '{"kind":"thumb"}\n\n{not-json}\n{"kind":"chat","text":"hi"}\n',
        encoding="utf-8",
    )
    out = store.read_recent_feedback(days=1)
    assert sorted(e["kind"] for e in out) == ["chat", "thumb"]


def test_read_recent_runs_dir_missing(tmp_data_dir):
    s = Store(tmp_data_dir)
    assert s.read_recent_runs(days=1) == []


def test_read_recent_runs_skips_bad_filenames_blanks_and_bad_json(store: Store):
    today = datetime.now(UTC).date()
    (store.runs_dir / "garbage.jsonl").write_text("{}\n", encoding="utf-8")
    (store.runs_dir / f"{today.isoformat()}.jsonl").write_text(
        '{"run_id":"a","started_at":"2026-05-14T10:00:00+00:00"}\n\n'
        "not json\n"
        '{"run_id":"b","started_at":"2026-05-14T11:00:00+00:00"}\n',
        encoding="utf-8",
    )
    out = store.read_recent_runs(days=1)
    assert [r["run_id"] for r in out] == ["b", "a"]


def test_read_recent_runs_filters_by_window(store: Store):
    today = datetime.now(UTC).date()
    (store.runs_dir / "2020-01-01.jsonl").write_text(
        '{"run_id":"old","started_at":"2020-01-01T10:00:00+00:00"}\n', encoding="utf-8"
    )
    (store.runs_dir / f"{today.isoformat()}.jsonl").write_text(
        '{"run_id":"new","started_at":"' + today.isoformat() + 'T10:00:00+00:00"}\n',
        encoding="utf-8",
    )
    out = store.read_recent_runs(days=7)
    assert [r["run_id"] for r in out] == ["new"]


def test_read_run_missing_dir_returns_none(tmp_data_dir):
    s = Store(tmp_data_dir)
    assert s.read_run("anything") is None


def test_read_run_skips_blank_and_invalid_json_lines(store: Store):
    today = datetime.now(UTC).date()
    (store.runs_dir / f"{today.isoformat()}.jsonl").write_text(
        '\nnot json\n{"run_id":"hit","kind":"curation"}\n', encoding="utf-8"
    )
    assert store.read_run("hit") == {"run_id": "hit", "kind": "curation"}
    assert store.read_run("miss") is None


def test_reflection_cursor_corrupt_json_returns_none(store: Store):
    store._cursor_path.parent.mkdir(parents=True, exist_ok=True)
    store._cursor_path.write_text("garbage", encoding="utf-8")
    assert store.read_reflection_cursor() is None


def test_reflection_cursor_non_string_value_returns_none(store: Store):
    import json

    store._cursor_path.parent.mkdir(parents=True, exist_ok=True)
    store._cursor_path.write_text(json.dumps({"last_processed_ts": 12345}), encoding="utf-8")
    assert store.read_reflection_cursor() is None


def test_reflection_lock_unreadable_stale_lock_not_stolen(store: Store):
    """A lock file with no expires_at is treated as 'don't know if stale' → don't steal."""
    store.state_dir.mkdir(parents=True, exist_ok=True)
    store._lock_path.write_text("garbage", encoding="utf-8")
    assert store.try_acquire_reflection_lock(ttl_seconds=900) is None


def test_release_reflection_lock_when_lock_file_absent(store: Store):
    """Releasing with no lock file is a no-op."""
    store.state_dir.mkdir(parents=True, exist_ok=True)
    store.release_reflection_lock("nope")  # must not raise


def test_release_reflection_lock_unreadable_lock_is_noop(store: Store):
    store.state_dir.mkdir(parents=True, exist_ok=True)
    store._lock_path.write_text("garbage", encoding="utf-8")
    store.release_reflection_lock("any-token")  # must not raise
    assert store._lock_path.exists()  # left alone


def test_git_init_if_needed_short_circuits_when_already_initialized(tmp_data_dir):
    """If .git already exists we don't reinitialize — the local config is kept."""
    s = Store(tmp_data_dir)
    s.git_init_if_needed()
    (tmp_data_dir / "marker").write_text("hi", encoding="utf-8")
    # Second call must not error and must not blow away our marker.
    s.git_init_if_needed()
    assert (tmp_data_dir / "marker").exists()


def test_release_reflection_lock_tolerates_file_disappearing(store: Store, monkeypatch):
    """Race: another process unlinks the lock between our exists() check and unlink()."""
    token = store.try_acquire_reflection_lock(ttl_seconds=900)
    assert token is not None

    real_unlink = type(store._lock_path).unlink

    def _vanishing(self):
        # Honest race: delete the file first, then re-raise the FileNotFoundError
        # the way a real concurrent unlink would.
        real_unlink(self)
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(type(store._lock_path), "unlink", _vanishing)
    # Must not raise.
    store.release_reflection_lock(token)


def test_git_commit_all_skipped_when_not_a_repo(tmp_data_dir):
    """git_commit_all returns False when the data dir hasn't been git-init'd."""
    s = Store(tmp_data_dir)
    s.ensure_layout()
    assert s.git_commit_all("msg") is False
