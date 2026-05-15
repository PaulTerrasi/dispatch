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


def test_ensure_layout_idempotent_does_not_overwrite(store: S3Store) -> None:
    """A second ensure_layout call must skip writes for objects already in place."""
    store.ensure_layout()
    store.write_profile("# customized\n")
    store.ensure_layout()  # must not blow away our customization
    assert store.read_profile() == "# customized\n"


def test_write_sources_youtube_with_tags(store: S3Store) -> None:
    """Tags are serialized when present on a YouTube source."""
    store.ensure_layout()
    store.write_sources([Source(kind="youtube", value="UCx", name="X", tags=["llm", "tooling"])])
    yt = next(s for s in store.list_sources() if s.kind == "youtube")
    assert yt.tags == ["llm", "tooling"]
    assert yt.name == "X"


def test_write_sources_youtube_without_name_or_tags(store: S3Store) -> None:
    """Round-trip a YouTube source missing both `name` and `tags`."""
    store.ensure_layout()
    store.write_sources([Source(kind="youtube", value="UCslim")])
    yt = next(s for s in store.list_sources() if s.kind == "youtube")
    assert yt.value == "UCslim"
    assert yt.name is None
    assert yt.tags == []


def test_write_sources_ignores_unknown_kind(store: S3Store) -> None:
    """Bogus kinds are dropped, not serialized."""
    store.ensure_layout()
    store.write_sources(
        [
            Source(kind="rss", value="https://ok.example/feed"),
            Source(kind="podcast", value="https://podcasts.example/feed"),
        ]
    )
    assert {s.kind for s in store.list_sources()} == {"rss"}


def test_profile_round_trip(store: S3Store) -> None:
    store.ensure_layout()
    store.write_profile("# Real profile\n\n## Standing interests\n- ML\n")
    assert "ML" in store.read_profile()


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


def test_reflection_cursor_round_trip(store: S3Store) -> None:
    assert store.read_reflection_cursor() is None
    store.write_reflection_cursor("2026-05-09T12:00:00+00:00")
    assert store.read_reflection_cursor() == "2026-05-09T12:00:00+00:00"


def test_reflection_memory_round_trip(store: S3Store) -> None:
    store.ensure_layout()
    assert "Reflection memory" in store.read_reflection_memory()
    store.write_reflection_memory("# Notes\n- watch source X\n")
    assert "watch source X" in store.read_reflection_memory()


# ── small-branch edge coverage ──────────────────────────────────────────────


def test_get_propagates_non_404_client_error(store: S3Store) -> None:
    """A real S3 outage (e.g. AccessDenied) must bubble up, not be swallowed."""
    from botocore.exceptions import ClientError

    err = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "no"}, "ResponseMetadata": {}},
        "GetObject",
    )

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise err

    store.s3.get_object = _boom  # type: ignore[assignment]
    with pytest.raises(ClientError):
        store.read_profile()


def test_list_sources_returns_empty_when_no_object(store: S3Store) -> None:
    """ensure_layout NOT called → sources.yaml doesn't exist → empty list."""
    assert store.list_sources() == []


def test_list_sources_handles_site_dicts_and_blanks(store: S3Store) -> None:
    """`sites:` entries may be plain strings or dicts; blanks are ignored."""
    store.s3.put_object(
        Bucket=BUCKET,
        Key="sources.yaml",
        Body=(
            b"rss: []\nyoutube: []\nsites:\n"
            b"  - https://a.example\n"
            b"  - url: https://b.example\n"
            b"  - {}\n"  # missing url → skipped
        ),
    )
    sites = [s for s in store.list_sources() if s.kind == "site"]
    # Site dict-form isn't widely used; the loop respects `url` key when present.
    assert "https://a.example" in {s.value for s in sites}


def test_list_digests_cache_returns_same_list_within_ttl(store: S3Store) -> None:
    """Two calls in quick succession should hit the in-memory cache."""
    store.ensure_layout()
    store.write_digest(date(2026, 5, 9), [], "")
    first = store.list_digests()
    # Out-of-band: add another digest behind the store's back.
    store.s3.put_object(
        Bucket=BUCKET,
        Key="digests/2026-05-10.json",
        Body=b'{"date":"2026-05-10","items":[],"agent_notes":""}',
    )
    # Still inside the 60s TTL → cached result, missing the new date.
    cached = store.list_digests()
    assert cached == first


def test_list_digests_skips_invalid_stems(store: S3Store) -> None:
    """Files like `digests/latest.json` shouldn't blow up the lister."""
    store.s3.put_object(Bucket=BUCKET, Key="digests/latest.json", Body=b"{}")
    store.s3.put_object(
        Bucket=BUCKET,
        Key="digests/2026-05-09.json",
        Body=b'{"date":"2026-05-09","items":[]}',
    )
    assert store.list_digests() == [date(2026, 5, 9)]


def test_read_digest_missing_returns_none(store: S3Store) -> None:
    assert store.read_digest(date(2020, 1, 1)) is None


def test_recent_digest_items_skips_dates_outside_window(store: S3Store) -> None:
    today = datetime.now(UTC).date()
    very_old = date(2020, 1, 1)
    item = {"id": "a", "title": "t", "source": "s", "url": "u"}
    store.write_digest(very_old, [item], "")
    store.write_digest(today, [{**item, "id": "b"}], "")
    out = store.recent_digest_items(days=7)
    assert [i["id"] for i in out] == ["b"]


def test_recent_digest_items_skips_empty_payload(store: S3Store) -> None:
    """A digest object with `null` content is treated as no items."""
    today = datetime.now(UTC).date()
    store.s3.put_object(Bucket=BUCKET, Key=f"digests/{today.isoformat()}.json", Body=b"null")
    # Force list_digests to refresh.
    store._list_cache = None
    assert store.recent_digest_items(days=7) == []


def test_update_item_feedback_no_digest_returns_false(store: S3Store) -> None:
    assert store.update_item_feedback(date(2020, 1, 1), "missing", "up") is False


def test_append_feedback_first_write_creates_object(store: S3Store) -> None:
    """First append must succeed even when the file doesn't exist yet."""
    store.append_feedback({"kind": "thumb", "value": "up", "item_id": "a"})
    events = store.read_recent_feedback(days=1)
    assert len(events) == 1


def test_append_feedback_propagates_non_404_get_error(
    store: S3Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the existing-file probe fails with something other than NoSuchKey we re-raise."""
    from botocore.exceptions import ClientError

    real_get = store.s3.get_object

    def _flaky(**kwargs: Any) -> Any:
        if kwargs.get("Key", "").startswith("feedback/"):
            raise ClientError(
                {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {}}, "GetObject"
            )
        return real_get(**kwargs)

    monkeypatch.setattr(store.s3, "get_object", _flaky)
    with pytest.raises(ClientError):
        store.append_feedback({"kind": "thumb", "value": "up"})


def test_read_recent_feedback_skips_dates_outside_window(store: S3Store) -> None:
    """Files older than the cutoff are listed but not fetched."""
    store.s3.put_object(Bucket=BUCKET, Key="feedback/2020-01-01.jsonl", Body=b'{"kind":"thumb"}\n')
    today = datetime.now(UTC).date().isoformat()
    store.s3.put_object(
        Bucket=BUCKET, Key=f"feedback/{today}.jsonl", Body=b'{"kind":"chat","text":"x"}\n'
    )
    out = store.read_recent_feedback(days=7)
    assert [e["kind"] for e in out] == ["chat"]


def test_read_recent_runs_skips_dates_outside_window(store: S3Store) -> None:
    store.s3.put_object(Bucket=BUCKET, Key="runs/2020-01-01.jsonl", Body=b'{"run_id":"old"}\n')
    today = datetime.now(UTC).date().isoformat()
    store.s3.put_object(
        Bucket=BUCKET,
        Key=f"runs/{today}.jsonl",
        Body=b'{"run_id":"new","started_at":"' + today.encode() + b'T10:00:00+00:00"}\n',
    )
    out = store.read_recent_runs(days=7)
    assert [r["run_id"] for r in out] == ["new"]


def test_read_recent_feedback_skips_invalid_stem_and_blank_lines(store: S3Store) -> None:
    store.s3.put_object(Bucket=BUCKET, Key="feedback/garbage.jsonl", Body=b"{}\n")
    today = datetime.now(UTC).date().isoformat()
    store.s3.put_object(
        Bucket=BUCKET,
        Key=f"feedback/{today}.jsonl",
        Body=b'{"kind":"thumb"}\n\nnot json\n{"kind":"chat","text":"x"}\n',
    )
    kinds = sorted(e["kind"] for e in store.read_recent_feedback(days=1))
    assert kinds == ["chat", "thumb"]


def test_read_recent_feedback_skips_unreadable_keys(
    store: S3Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a single feedback file disappears between list and get, skip it."""
    today = datetime.now(UTC).date().isoformat()
    store.s3.put_object(Bucket=BUCKET, Key=f"feedback/{today}.jsonl", Body=b'{"kind":"thumb"}\n')

    real_get = store._get

    def _maybe_missing(key: str) -> str | None:
        if key.startswith("feedback/"):
            return None
        return real_get(key)

    monkeypatch.setattr(store, "_get", _maybe_missing)
    assert store.read_recent_feedback(days=1) == []


def test_append_run_propagates_non_404_get_error(
    store: S3Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from botocore.exceptions import ClientError

    real_get = store.s3.get_object

    def _flaky(**kwargs: Any) -> Any:
        if kwargs.get("Key", "").startswith("runs/"):
            raise ClientError(
                {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {}}, "GetObject"
            )
        return real_get(**kwargs)

    monkeypatch.setattr(store.s3, "get_object", _flaky)
    with pytest.raises(ClientError):
        store.append_run({"run_id": "x", "started_at": "2026-05-09T10:00:00+00:00"})


def test_read_recent_runs_skips_bad_filenames_blanks_and_bad_json(store: S3Store) -> None:
    today = datetime.now(UTC).date().isoformat()
    store.s3.put_object(Bucket=BUCKET, Key="runs/latest.jsonl", Body=b"{}\n")
    store.s3.put_object(
        Bucket=BUCKET,
        Key=f"runs/{today}.jsonl",
        Body=(
            b'{"run_id":"a","started_at":"' + today.encode() + b'T10:00:00+00:00"}\n'
            b"\nnot json\n"
            b'{"run_id":"b","started_at":"' + today.encode() + b'T11:00:00+00:00"}\n'
        ),
    )
    assert [r["run_id"] for r in store.read_recent_runs(days=1)] == ["b", "a"]


def test_read_recent_runs_skips_unreadable_keys(
    store: S3Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = datetime.now(UTC).date().isoformat()
    store.s3.put_object(Bucket=BUCKET, Key=f"runs/{today}.jsonl", Body=b'{"run_id":"r"}\n')
    real_get = store._get
    monkeypatch.setattr(store, "_get", lambda k: None if k.startswith("runs/") else real_get(k))
    assert store.read_recent_runs(days=1) == []


def test_read_run_handles_blanks_and_bad_json(store: S3Store) -> None:
    today = datetime.now(UTC).date().isoformat()
    store.s3.put_object(
        Bucket=BUCKET,
        Key=f"runs/{today}.jsonl",
        Body=b'\nnot json\n{"run_id":"hit","kind":"curation"}\n',
    )
    assert store.read_run("hit") == {"run_id": "hit", "kind": "curation"}
    assert store.read_run("miss") is None


def test_read_run_skips_unreadable_keys(store: S3Store, monkeypatch: pytest.MonkeyPatch) -> None:
    today = datetime.now(UTC).date().isoformat()
    store.s3.put_object(Bucket=BUCKET, Key=f"runs/{today}.jsonl", Body=b'{"run_id":"r"}\n')
    real_get = store._get
    monkeypatch.setattr(store, "_get", lambda k: None if k.startswith("runs/") else real_get(k))
    assert store.read_run("r") is None


def test_reflection_cursor_corrupt_json_returns_none(store: S3Store) -> None:
    store.s3.put_object(Bucket=BUCKET, Key="state/reflection_cursor.json", Body=b"garbage")
    assert store.read_reflection_cursor() is None


def test_reflection_cursor_non_string_ts_returns_none(store: S3Store) -> None:
    store.s3.put_object(
        Bucket=BUCKET, Key="state/reflection_cursor.json", Body=b'{"last_processed_ts": 1}'
    )
    assert store.read_reflection_cursor() is None


# ── lock methods: drive boto3 directly with fakes ──────────────────────────


class _FakeS3:
    """Minimal in-memory S3 that supports IfNoneMatch/IfMatch — moto does not."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}  # key -> (body, etag)
        self._etag_counter = 0

    def _new_etag(self) -> str:
        self._etag_counter += 1
        return f"etag-{self._etag_counter}"

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str = "",
        IfNoneMatch: str | None = None,
        IfMatch: str | None = None,
    ) -> dict[str, str]:
        del Bucket, ContentType
        from botocore.exceptions import ClientError

        if IfNoneMatch == "*" and Key in self.objects:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {}},
                "PutObject",
            )
        if IfMatch is not None:
            existing = self.objects.get(Key)
            if existing is None or existing[1] != IfMatch:
                raise ClientError(
                    {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {}},
                    "PutObject",
                )
        etag = self._new_etag()
        self.objects[Key] = (Body, etag)
        return {"ETag": etag}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        del Bucket
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {}}, "GetObject")
        body, etag = self.objects[Key]

        class _Body:
            def read(self_inner) -> bytes:
                return body

        return {"Body": _Body(), "ETag": etag}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        del Bucket
        self.objects.pop(Key, None)
        return {}


@pytest.fixture
def fake_store() -> S3Store:
    """An S3Store with the boto3 client swapped for the in-memory _FakeS3."""
    s = S3Store.__new__(S3Store)
    s.bucket = "fake"
    s.s3 = _FakeS3()  # type: ignore[assignment]
    s._digest_cache = {}  # type: ignore[attr-defined]
    s._list_cache = None  # type: ignore[attr-defined]
    s._list_cache_ts = 0.0  # type: ignore[attr-defined]
    return s


def test_lock_acquire_creates_then_blocks_then_releases(fake_store: S3Store) -> None:
    """Happy path: first acquire wins (IfNoneMatch=*), second is blocked, release frees."""
    t1 = fake_store.try_acquire_reflection_lock(ttl_seconds=900)
    assert t1 is not None
    t2 = fake_store.try_acquire_reflection_lock(ttl_seconds=900)
    assert t2 is None
    fake_store.release_reflection_lock(t1)
    t3 = fake_store.try_acquire_reflection_lock(ttl_seconds=900)
    assert t3 is not None


def test_lock_steals_stale_lock(fake_store: S3Store) -> None:
    """Expired locks should be stolen via the IfMatch conditional put."""
    import json

    fake_store.s3.objects[S3Store._LOCK_KEY] = (  # type: ignore[attr-defined]
        json.dumps(
            {
                "token": "stale",
                "started_at": "2020-01-01T00:00:00+00:00",
                "expires_at": "2020-01-01T00:15:00+00:00",
            }
        ).encode(),
        "etag-99",
    )
    token = fake_store.try_acquire_reflection_lock(ttl_seconds=900)
    assert token is not None and token != "stale"


def test_lock_does_not_steal_when_still_valid(fake_store: S3Store) -> None:
    """A lock not yet expired must NOT be stolen."""
    import json
    from datetime import timedelta

    future = (datetime.now(UTC) + timedelta(seconds=600)).isoformat()
    fake_store.s3.objects[S3Store._LOCK_KEY] = (  # type: ignore[attr-defined]
        json.dumps({"token": "ok", "started_at": "now", "expires_at": future}).encode(),
        "etag-77",
    )
    assert fake_store.try_acquire_reflection_lock(ttl_seconds=900) is None


def test_lock_returns_none_when_existing_lock_is_unreadable(fake_store: S3Store) -> None:
    """A garbage lock file should be treated as 'unknown state' → don't steal."""
    fake_store.s3.objects[S3Store._LOCK_KEY] = (b"garbage", "etag-bad")  # type: ignore[attr-defined]
    assert fake_store.try_acquire_reflection_lock(ttl_seconds=900) is None


def test_lock_returns_none_when_existing_lock_missing_expires_at(fake_store: S3Store) -> None:
    """A lock dict missing `expires_at` is treated as unknown → not stolen."""
    import json

    fake_store.s3.objects[S3Store._LOCK_KEY] = (  # type: ignore[attr-defined]
        json.dumps({"token": "x"}).encode(),
        "etag-mis",
    )
    assert fake_store.try_acquire_reflection_lock(ttl_seconds=900) is None


def test_lock_acquire_raises_on_unexpected_put_error(fake_store: S3Store) -> None:
    """Non-PreconditionFailed put errors should bubble up — they're real outages."""
    from botocore.exceptions import ClientError

    def _boom(**_kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "InternalError"}, "ResponseMetadata": {}}, "PutObject")

    fake_store.s3.put_object = _boom  # type: ignore[attr-defined,assignment]
    with pytest.raises(ClientError):
        fake_store.try_acquire_reflection_lock(ttl_seconds=900)


def test_lock_steal_get_failure_returns_none(fake_store: S3Store) -> None:
    """If GET on the existing lock fails right after PUT contention, give up cleanly."""
    from botocore.exceptions import ClientError

    fake_store.s3.objects[S3Store._LOCK_KEY] = (b"{}", "etag-1")  # type: ignore[attr-defined]
    real_get = fake_store.s3.get_object

    def _flaky(**kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "Throttled"}, "ResponseMetadata": {}}, "GetObject")

    fake_store.s3.get_object = _flaky  # type: ignore[attr-defined,assignment]
    assert fake_store.try_acquire_reflection_lock(ttl_seconds=900) is None
    fake_store.s3.get_object = real_get  # type: ignore[attr-defined,assignment]


def test_lock_steal_etag_race_returns_none(fake_store: S3Store) -> None:
    """If another worker steals the lock between our GET and IfMatch PUT, return None."""
    import json
    from datetime import timedelta

    expired = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    fake_store.s3.objects[S3Store._LOCK_KEY] = (  # type: ignore[attr-defined]
        json.dumps({"token": "stale", "expires_at": expired}).encode(),
        "etag-old",
    )
    # Mutate underneath after the GET happens: swap put_object to always fail.
    real_put = fake_store.s3.put_object

    def _put_simulating_race(**kwargs: Any) -> Any:
        from botocore.exceptions import ClientError

        if kwargs.get("IfMatch") == "etag-old":
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {}},
                "PutObject",
            )
        return real_put(**kwargs)

    fake_store.s3.put_object = _put_simulating_race  # type: ignore[attr-defined,assignment]
    assert fake_store.try_acquire_reflection_lock(ttl_seconds=900) is None


def test_release_when_lock_missing_is_noop(fake_store: S3Store) -> None:
    """release_reflection_lock with no lock object outstanding must not raise."""
    fake_store.release_reflection_lock("nope")


def test_release_with_unparseable_lock_is_noop(fake_store: S3Store) -> None:
    fake_store.s3.objects[S3Store._LOCK_KEY] = (b"garbage", "e")  # type: ignore[attr-defined]
    fake_store.release_reflection_lock("any")  # must not raise
    assert S3Store._LOCK_KEY in fake_store.s3.objects  # type: ignore[attr-defined]


def test_release_wrong_token_leaves_lock_alone(fake_store: S3Store) -> None:
    import json

    fake_store.s3.objects[S3Store._LOCK_KEY] = (  # type: ignore[attr-defined]
        json.dumps({"token": "real"}).encode(),
        "e",
    )
    fake_store.release_reflection_lock("imposter")
    assert S3Store._LOCK_KEY in fake_store.s3.objects  # type: ignore[attr-defined]


def test_release_handles_delete_failure_silently(
    fake_store: S3Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An eventually-consistent S3 may have the lock vanish before delete; ignore."""
    import json

    fake_store.s3.objects[S3Store._LOCK_KEY] = (  # type: ignore[attr-defined]
        json.dumps({"token": "mine"}).encode(),
        "e",
    )

    from botocore.exceptions import ClientError

    def _boom(**_kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {}}, "DeleteObject")

    monkeypatch.setattr(fake_store.s3, "delete_object", _boom)
    fake_store.release_reflection_lock("mine")  # must not raise


def test_git_methods_are_noops(store: S3Store) -> None:
    """S3 versioning replaces git; the methods exist to satisfy the protocol."""
    store.git_init_if_needed()  # should not raise
    assert store.git_commit_all("anything") in (False, None)
