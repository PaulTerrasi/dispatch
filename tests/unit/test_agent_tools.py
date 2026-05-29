"""Tests for the curation- and reflection-phase SDK tools wired up in
`digest.agent`.

These exercise both the happy path and the catch-all error branches that
surface fetcher failures as `isError: True` tool responses, plus the small
helpers (`_compact_curation_runs`, `_stable_id`, talk-tool lock wrapping).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from digest.agent import (
    RunState,
    _compact_curation_runs,
    _stable_id,
    build_chat_tools,
    build_curation_tools,
    build_reflection_tools,
    chat_options,
    curation_options,
    reflection_options,
)
from digest.store import Store


def _state(store: Store) -> RunState:
    return RunState(store=store, today=date(2026, 5, 14), run_id="rid12345")


# ── _stable_id + run state.record ───────────────────────────────────────────


def test_stable_id_is_deterministic_and_8_hex_chars() -> None:
    a = _stable_id("https://x", "Title")
    b = _stable_id("https://x", "Title")
    assert a == b
    assert len(a) == 8
    assert all(c in "0123456789abcdef" for c in a)
    assert _stable_id("https://y", "Title") != a


def test_runstate_record_attaches_pending_thinking_then_clears(store: Store) -> None:
    state = _state(store)
    state.pending_thinking = "think hard"
    state.record("read_profile", {}, "ok")
    assert state.tool_log[-1]["thinking"] == "think hard"
    assert state.pending_thinking is None
    # Next record: no thinking attached.
    state.record("list_sources", {}, "ok")
    assert "thinking" not in state.tool_log[-1]


def test_runstate_record_strips_html_arg(store: Store) -> None:
    state = _state(store)
    state.record("web_fetch", {"url": "u", "html": "<a>" * 1000}, "ok")
    assert "html" not in state.tool_log[-1]["args"]


# ── Curation tools: happy path, error branches ─────────────────────────────


@pytest.mark.asyncio
async def test_curation_list_sources_empty_and_populated(store: Store) -> None:
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["list_sources"]({})
    assert out["content"][0]["text"] == "(no sources configured)"

    from digest.store import Source

    store.write_sources([Source(kind="rss", value="https://x.example/feed", tags=["t"])])
    out = await state.current_tools["list_sources"]({})
    assert "rss\thttps://x.example/feed" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_curation_fetch_rss_success(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    from digest import agent as agent_mod
    from digest.tools.rss import FeedEntry

    async def _fake_rss(url: str, *, limit: int = 25) -> list[FeedEntry]:
        return [FeedEntry(title="T", url="u", published=None, summary="", source_title="src")]

    monkeypatch.setattr(agent_mod, "fetch_rss", _fake_rss)
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["fetch_rss"]({"url": "https://x.example/feed"})
    assert not out.get("isError")
    assert '"title": "T"' in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_curation_fetch_rss_truncates_long_summaries_in_details(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Long feed summaries get capped at 2000 chars in the stored details
    payload so a chatty feed doesn't bloat the run record."""
    from digest import agent as agent_mod
    from digest.tools.rss import FeedEntry

    long_summary = "x" * 5000

    async def _fake_rss(url: str, *, limit: int = 25) -> list[FeedEntry]:
        return [
            FeedEntry(title="T", url="u", published=None, summary=long_summary, source_title="src")
        ]

    monkeypatch.setattr(agent_mod, "fetch_rss", _fake_rss)
    state = _state(store)
    build_curation_tools(state)
    await state.current_tools["fetch_rss"]({"url": "https://x"})
    entry = state.tool_log[-1]
    stored = entry["details"]["entries"][0]["summary"]
    assert len(stored) == 2000


@pytest.mark.asyncio
async def test_curation_fetch_rss_error_returns_isError(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from digest import agent as agent_mod

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("DNS exploded")

    monkeypatch.setattr(agent_mod, "fetch_rss", _boom)
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["fetch_rss"]({"url": "https://x"})
    assert out.get("isError") is True
    assert "DNS exploded" in out["content"][0]["text"]
    # Failed tool calls still get logged so the run trail is complete.
    assert state.tool_log[-1]["outcome"].startswith("error:")


@pytest.mark.asyncio
async def test_curation_fetch_youtube_channel_error(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from digest import agent as agent_mod

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("youtube down")

    monkeypatch.setattr(agent_mod, "fetch_youtube_channel", _boom)
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["fetch_youtube_channel"]({"channel_id": "UCabc"})
    assert out.get("isError") is True


@pytest.mark.asyncio
async def test_curation_fetch_youtube_channel_success(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from digest import agent as agent_mod
    from digest.tools.rss import FeedEntry

    async def _ok(*_a: Any, **_k: Any) -> Any:
        return [FeedEntry(title="V", url="u", published=None, summary="", source_title="ch")]

    monkeypatch.setattr(agent_mod, "fetch_youtube_channel", _ok)
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["fetch_youtube_channel"]({"channel_id": "UCabc"})
    assert not out.get("isError")


@pytest.mark.asyncio
async def test_curation_fetch_youtube_transcript_success(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from digest import agent as agent_mod
    from digest.tools.youtube import TranscriptResult

    async def _ok(_vid: str) -> TranscriptResult:
        return TranscriptResult(video_id="v", text="hello world", language="en")

    monkeypatch.setattr(agent_mod, "fetch_youtube_transcript", _ok)
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["fetch_youtube_transcript"]({"video_id": "v"})
    assert "hello world" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_curation_fetch_youtube_transcript_error(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from digest import agent as agent_mod

    async def _boom(_vid: str) -> Any:
        raise RuntimeError("no captions")

    monkeypatch.setattr(agent_mod, "fetch_youtube_transcript", _boom)
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["fetch_youtube_transcript"]({"video_id": "v"})
    assert out.get("isError") is True


@pytest.mark.asyncio
async def test_curation_web_fetch_success_and_error(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from digest import agent as agent_mod
    from digest.tools.web_fetch import WebDocument

    async def _ok(_url: str) -> WebDocument:
        return WebDocument(url="u", title="T", text="hello", html="<p>hello</p>")

    monkeypatch.setattr(agent_mod, "web_fetch", _ok)
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["web_fetch"]({"url": "https://x"})
    assert "hello" in out["content"][0]["text"]

    async def _boom(_url: str) -> Any:
        raise RuntimeError("timeout")

    monkeypatch.setattr(agent_mod, "web_fetch", _boom)
    out = await state.current_tools["web_fetch"]({"url": "https://x"})
    assert out.get("isError") is True


@pytest.mark.asyncio
async def test_submit_digest_rejects_non_list_items(store: Store) -> None:
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["submit_digest"]({"items": "not-a-list", "agent_notes": ""})
    assert out.get("isError") is True


@pytest.mark.asyncio
async def test_submit_digest_filters_garbage_and_dedupes(store: Store) -> None:
    state = _state(store)
    build_curation_tools(state)
    items = [
        "not a dict",  # skipped (non-dict)
        {"title": "no url"},  # skipped
        {"url": "https://a", "title": ""},  # skipped (no title)
        {
            "url": "https://a",
            "title": "Real",
            "source": "s",
            "summary": "ok",
            "type": "article",
        },
        {
            "url": "https://a",
            "title": "Real",
            "source": "s",
            "summary": "duplicate id",
        },  # dedupe
        {
            "url": "https://b",
            "title": "Video",
            "type": "video",
            "duration_min": "12",  # coerced to int
        },
        {
            "url": "https://c",
            "title": "Bad duration",
            "duration_min": "not-an-int",  # silently dropped
        },
    ]
    out = await state.current_tools["submit_digest"]({"items": items, "agent_notes": "n"})
    assert not out.get("isError")
    assert state.submitted_items is not None
    titles = [i["title"] for i in state.submitted_items]
    assert titles == ["Real", "Video", "Bad duration"]
    video = next(i for i in state.submitted_items if i["title"] == "Video")
    assert video["duration_min"] == 12
    assert "duration_min" not in next(
        i for i in state.submitted_items if i["title"] == "Bad duration"
    )


@pytest.mark.asyncio
async def test_submit_digest_defaults_type_to_article(store: Store) -> None:
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["submit_digest"](
        {"items": [{"url": "https://a", "title": "T"}], "agent_notes": ""}
    )
    assert not out.get("isError")
    assert state.submitted_items[0]["type"] == "article"  # type: ignore[index]


# ── Reflection tools: error branches ───────────────────────────────────────


@pytest.mark.asyncio
async def test_reflection_edit_profile_rejects_missing_find(store: Store) -> None:
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["edit_profile"]({"find": "nope-not-in-profile", "replace": "x"})
    assert out.get("isError") is True
    assert state.profile_patches_applied == 0


@pytest.mark.asyncio
async def test_reflection_edit_profile_rejects_empty_find(store: Store) -> None:
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["edit_profile"]({"find": "", "replace": "x"})
    assert out.get("isError") is True
    assert "empty" in out["content"][0]["text"]
    assert state.profile_patches_applied == 0


@pytest.mark.asyncio
async def test_reflection_edit_profile_rejects_non_unique_find(store: Store) -> None:
    state = _state(store)
    store.write_profile("- a\n- a\n- b\n")
    build_reflection_tools(state)
    out = await state.current_tools["edit_profile"]({"find": "- a", "replace": "- A"})
    assert out.get("isError") is True
    assert "appears" in out["content"][0]["text"]
    assert state.profile_patches_applied == 0


@pytest.mark.asyncio
async def test_reflection_add_source_rejects_invalid_kind(store: Store) -> None:
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["add_source"]({"kind": "bogus", "value": "x"})
    assert out.get("isError") is True
    assert "invalid kind" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_reflection_add_source_requires_value(store: Store) -> None:
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["add_source"]({"kind": "rss", "value": ""})
    assert out.get("isError") is True


@pytest.mark.asyncio
async def test_reflection_add_source_normalizes_tags(store: Store) -> None:
    """Tags arrives as a list; falsy entries are dropped, others stringified."""
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["add_source"](
        {
            "kind": "rss",
            "value": "https://x.example/feed",
            "name": "X",
            "tags": ["llm", "", None, 42],
        }
    )
    assert not out.get("isError")
    rss = next(s for s in store.list_sources() if s.kind == "rss")
    assert rss.tags == ["llm", "42"]


@pytest.mark.asyncio
async def test_reflection_add_source_ignores_non_list_tags(store: Store) -> None:
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["add_source"](
        {"kind": "rss", "value": "https://x", "tags": "not-a-list"}
    )
    assert not out.get("isError")
    rss = next(s for s in store.list_sources() if s.kind == "rss")
    assert rss.tags == []


@pytest.mark.asyncio
async def test_reflection_remove_source_not_found_is_error(store: Store) -> None:
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["remove_source"]({"kind": "rss", "value": "https://nope"})
    assert out.get("isError") is True


@pytest.mark.asyncio
async def test_reflection_read_recent_feedback_requires_triggering_event(
    store: Store,
) -> None:
    state = _state(store)
    build_reflection_tools(state)
    # No triggering_event set → handler asserts.
    with pytest.raises(AssertionError):
        await state.current_tools["read_recent_feedback"]({"days": 14})


@pytest.mark.asyncio
async def test_reflection_read_recent_curation_runs_filters_to_curation(store: Store) -> None:
    """A mixed runs window should produce only curation entries in the output."""
    today = datetime.now(UTC).date().isoformat()
    store.append_run(
        {
            "run_id": "c1",
            "kind": "curation",
            "started_at": f"{today}T08:00:00+00:00",
            "item_count": 2,
            "tool_log": [],
        }
    )
    store.append_run(
        {
            "run_id": "r1",
            "kind": "reflection",
            "started_at": f"{today}T09:00:00+00:00",
            "tool_log": [],
        }
    )
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["read_recent_curation_runs"]({"days": 7})
    text = out["content"][0]["text"]
    assert "c1" in text
    assert "r1" not in text


# ── _compact_curation_runs ─────────────────────────────────────────────────


def test_compact_curation_runs_empty() -> None:
    assert _compact_curation_runs([], max_bytes=1000) == "(no curation runs in window)"


def test_compact_curation_runs_truncates_when_over_budget() -> None:
    """Once the byte budget is exceeded, remaining runs are summarized as truncated."""
    runs = [
        {
            "run_id": f"r{i}",
            "started_at": "2026-05-14T08:00:00+00:00",
            "item_count": 1,
            "tool_log": [
                {"tool": "read_profile", "args": {}, "outcome": "x" * 100} for _ in range(5)
            ],
        }
        for i in range(20)
    ]
    out = _compact_curation_runs(runs, max_bytes=500)
    assert "older runs truncated" in out


def test_compact_curation_runs_keeps_short_thinking_verbatim() -> None:
    """Thinking text ≤200 chars is preserved without ellipsis."""
    run = {
        "run_id": "r1",
        "started_at": "2026-05-14T08:00:00+00:00",
        "item_count": 1,
        "tool_log": [{"tool": "list_sources", "args": {}, "outcome": "ok", "thinking": "short"}],
    }
    out = _compact_curation_runs([run], max_bytes=10_000)
    assert "thinking: short" in out
    assert "…" not in out


def test_compact_curation_runs_includes_thinking_and_strips_large_args() -> None:
    run = {
        "run_id": "r1",
        "started_at": "2026-05-14T08:00:00+00:00",
        "item_count": 1,
        "tool_log": [
            {
                "tool": "fetch_rss",
                "args": {"url": "u", "html": "<huge>", "text": "x" * 1000, "ok": "fine"},
                "outcome": "ok",
                "thinking": "line1\n" + "y" * 300,
            }
        ],
    }
    out = _compact_curation_runs([run], max_bytes=10_000)
    assert "html" not in out
    assert "fine" in out  # short args kept
    assert "thinking:" in out
    # Long thinking is truncated with ellipsis.
    assert "…" in out


# ── Options builders & chat_options ────────────────────────────────────────


def test_curation_options_captures_system_prompt_in_state(store: Store) -> None:
    state = _state(store)
    try:
        opts = curation_options(state)
        assert state.curation_system_prompt
        # Spilled to a file so the rendered prompt (which can be large enough to
        # blow past ARG_MAX) doesn't ride on the CLI argv.
        assert isinstance(opts.system_prompt, dict)
        assert opts.system_prompt["type"] == "file"
        assert Path(opts.system_prompt["path"]).read_text() == state.curation_system_prompt
        assert "mcp__digest__submit_digest" in opts.allowed_tools
        # Read tools are now baked into the system prompt, not exposed as tools.
        assert "mcp__digest__read_profile" not in opts.allowed_tools
        assert "mcp__digest__read_recent_feedback" not in opts.allowed_tools
        assert "mcp__digest__read_recent_digests" not in opts.allowed_tools
    finally:
        state.cleanup_temp_dirs()


def test_curation_options_cleanup_removes_spilled_prompt_dir(store: Store) -> None:
    state = _state(store)
    opts = curation_options(state)
    assert isinstance(opts.system_prompt, dict)
    prompt_path = Path(opts.system_prompt["path"])
    assert prompt_path.exists()
    state.cleanup_temp_dirs()
    assert not prompt_path.exists()
    assert not prompt_path.parent.exists()


def test_fill_template_does_not_bleed_values_into_each_other(store: Store) -> None:
    """If a value contains another placeholder, it must NOT get expanded —
    single-pass substitution prevents user-controlled profile text from
    injecting digest rows into itself."""
    from digest.agent import _fill_template

    out = _fill_template(
        "P={{PROFILE}} D={{RECENT_DIGESTS}}",
        {"PROFILE": "hostile {{RECENT_DIGESTS}}", "RECENT_DIGESTS": "real-digest"},
    )
    assert out == "P=hostile {{RECENT_DIGESTS}} D=real-digest"


def test_render_recent_digests_handles_missing_digest_data(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If list_digests returns a date but read_digest comes back None for it
    (e.g. a corrupt file mid-pipeline), the row is skipped rather than crashing."""
    from digest.agent import _render_recent_digests_with_feedback

    today = datetime.now(UTC).date()
    monkeypatch.setattr(store, "list_digests", lambda: [today])
    monkeypatch.setattr(store, "read_digest", lambda _d: None)
    state = RunState(store=store, today=today)
    out = _render_recent_digests_with_feedback(state, days=21)
    assert "no items surfaced in the last 21 days" in out


def test_render_recent_digests_includes_only_within_window(store: Store) -> None:
    """Old digests outside the window are skipped (and the loop breaks early
    because list_digests is sorted newest-first)."""
    from datetime import timedelta as _td

    from digest.agent import _render_recent_digests_with_feedback

    today = date(2026, 5, 14)
    old = today - _td(days=30)
    store.write_digest(
        today,
        [{"id": "n", "title": "new", "source": "x.com", "url": "https://x.com/n"}],
        "",
    )
    store.write_digest(
        old,
        [{"id": "o", "title": "old", "source": "x.com", "url": "https://x.com/o"}],
        "",
    )
    state = RunState(store=store, today=today)
    out = _render_recent_digests_with_feedback(state, days=21)
    assert "new" in out
    assert "old" not in out


def test_curation_options_injects_profile_and_recent_digests(store: Store) -> None:
    """Profile text and recent digest items (with feedback) must be substituted
    into the system prompt so the agent has them up front."""
    store.write_profile("# Profile\n- LLMs and woodworking\n")
    today = datetime.now(UTC).date()
    store.write_digest(
        today,
        [
            {
                "id": "abc12345",
                "title": "A great post",
                "source": "src.com",
                "url": "https://src.com/a",
                "feedback": "up",
            }
        ],
        "",
    )
    state = RunState(store=store, today=today, run_id="rid")
    try:
        opts = curation_options(state)
        rendered = Path(opts.system_prompt["path"]).read_text()
        assert "LLMs and woodworking" in rendered
        assert "A great post" in rendered
        assert "src.com" in rendered
        assert "up" in rendered
        assert state.profile_snapshot.startswith("# Profile")
        assert "{{PROFILE}}" not in rendered
        assert "{{RECENT_DIGESTS}}" not in rendered
    finally:
        state.cleanup_temp_dirs()


def test_reflection_options_captures_system_prompt(store: Store) -> None:
    state = _state(store)
    opts = reflection_options(state)
    assert state.reflection_system_prompt
    assert opts.system_prompt == state.reflection_system_prompt


def test_chat_options_uses_partial_messages(store: Store) -> None:
    state = _state(store)
    q: asyncio.Queue[str] = asyncio.Queue()
    opts = chat_options(state, q)
    assert opts.include_partial_messages is True
    # The stderr callback is installed by routes_chat per request so it can
    # buffer the lines for the failure SSE; chat_options must leave it unset.
    assert opts.stderr is None


# ── Chat tools: lock wrapping for the write toolkit ────────────────────────


@pytest.mark.asyncio
async def test_talk_tools_read_only_methods_are_unwrapped(store: Store) -> None:
    """Only edit_profile/add_source/remove_source are wrapped; reads are passthrough."""
    state = _state(store)
    q: asyncio.Queue[str] = asyncio.Queue()
    build_chat_tools(state, q)
    # Successful read_profile should not emit a profile_changed event.
    await state.current_tools["read_profile"]({})
    assert q.empty()


@pytest.mark.asyncio
async def test_talk_tools_write_failure_does_not_emit_profile_changed(store: Store) -> None:
    """If the underlying write returns isError, no SSE notification should fire."""
    state = _state(store)
    q: asyncio.Queue[str] = asyncio.Queue()
    build_chat_tools(state, q)
    # Adding a duplicate source returns isError without changing storage.
    from digest.store import Source

    store.write_sources([Source(kind="rss", value="https://x.example/feed")])
    out = await state.current_tools["add_source"](
        {"kind": "rss", "value": "https://x.example/feed"}
    )
    assert out.get("isError") is True
    assert q.empty()


@pytest.mark.asyncio
async def test_talk_tools_lock_released_after_write(store: Store) -> None:
    """After a successful talk-write the lock should be released for the next caller."""
    state = _state(store)
    q: asyncio.Queue[str] = asyncio.Queue()
    build_chat_tools(state, q)
    # First write succeeds.
    out = await state.current_tools["add_source"](
        {"kind": "rss", "value": "https://x.example/feed"}
    )
    assert not out.get("isError")
    assert q.get_nowait() == "add_source"
    # Lock must be free — try acquiring directly.
    token = store.try_acquire_reflection_lock(ttl_seconds=60)
    assert token is not None
    store.release_reflection_lock(token)


@pytest.mark.asyncio
async def test_reflection_read_recent_digests(store: Store) -> None:
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["read_recent_digests"]({})
    assert out["content"][0]["text"] == "(none)"

    today = datetime.now(UTC).date()
    store.write_digest(
        today,
        [{"id": "1", "title": "Title", "source": "src.com", "url": "https://src.com/a"}],
        "",
    )
    out = await state.current_tools["read_recent_digests"]({"days": 14})
    assert "Title\tsrc.com" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_reflection_read_recent_digests_caps_items_at_50(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """details['items'] should be sliced to at most 50 entries even if the
    store has more, so the run record stays bounded for wide days windows."""
    fake_items = [
        {"date": "2026-05-10", "id": str(i), "title": "t", "source": "s", "url": "u"}
        for i in range(100)
    ]
    monkeypatch.setattr(store, "recent_digest_items", lambda *, days: fake_items)
    state = _state(store)
    build_reflection_tools(state)
    await state.current_tools["read_recent_digests"]({"days": 30})
    entry = state.tool_log[-1]
    assert len(entry["details"]["items"]) == 50


@pytest.mark.asyncio
async def test_submit_digest_details_strip_internal_fields(store: Store) -> None:
    """details.items should drop the 'feedback' and 'run_id' bookkeeping keys
    that the digest store needs but the run-detail view has no use for."""
    state = _state(store)
    build_curation_tools(state)
    await state.current_tools["submit_digest"](
        {
            "items": [
                {
                    "type": "article",
                    "title": "T",
                    "source": "s",
                    "url": "https://x/a",
                    "summary": "ok",
                }
            ],
            "agent_notes": "n",
        }
    )
    entry = state.tool_log[-1]
    item = entry["details"]["items"][0]
    assert "feedback" not in item
    assert "run_id" not in item
    # The non-stripped fields are still there.
    assert item["title"] == "T"


@pytest.mark.asyncio
async def test_read_triggering_curation_run_skips_legacy_runs(store: Store) -> None:
    """Legacy curation runs (no `submitted_item_ids` field) can't be matched —
    the recorded args carry only a "count", not the item ids, and there's no
    reverse index. The tool skips such runs and returns the not-found sentinel."""
    today = datetime.now(UTC).date().isoformat()
    store.append_run(
        {
            "run_id": "old",
            "kind": "curation",
            "started_at": f"{today}T08:00:00+00:00",
            "tool_log": [
                {"tool": "fetch_rss", "args": {"url": "u"}, "outcome": "ok"},
                {"tool": "submit_digest", "args": {"count": 1}, "outcome": "ok"},
            ],
        }
    )
    state = _state(store)
    state.triggering_event = {
        "ts": f"{today}T10:00:00+00:00",
        "kind": "thumb",
        "value": "down",
        "item_id": "abc",
    }
    build_reflection_tools(state)
    out = await state.current_tools["read_triggering_curation_run"]({})
    # Match not found via legacy fallback (intentional).
    assert "no curation run" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_scripted_runner_raises_on_unknown_tool(store: Store) -> None:
    """The scripted runner used by tests rejects tools that aren't registered
    for the current phase — this guards against tests drifting against the
    agent surface."""
    from digest.agent import ScriptedAgentRunner

    state = _state(store)
    build_curation_tools(state)
    runner = ScriptedAgentRunner(state=state, script=[("bogus_tool", {})])

    with pytest.raises(RuntimeError, match="bogus_tool"):
        async for _ in runner.run("prompt", curation_options(state)):
            pass


@pytest.mark.asyncio
async def test_sdk_agent_runner_delegates_to_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """SdkAgentRunner.run yields whatever `claude_agent_sdk.query` produces."""
    from digest import agent as agent_mod

    async def _fake_query(*, prompt: str, options: Any) -> Any:
        assert prompt == "p"
        yield {"type": "result", "scripted": False}
        yield {"type": "result", "scripted": False, "done": True}

    monkeypatch.setattr(agent_mod, "query", _fake_query)
    runner = agent_mod.SdkAgentRunner()
    results: list[Any] = []
    async for msg in runner.run("p", agent_mod.ClaudeAgentOptions()):
        results.append(msg)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_talk_remove_source_through_lock_wrapping(store: Store) -> None:
    """remove_source is also lock-wrapped; success should fire profile_changed."""
    from digest.store import Source

    store.write_sources([Source(kind="rss", value="https://gone.example/feed")])
    state = _state(store)
    q: asyncio.Queue[str] = asyncio.Queue()
    build_chat_tools(state, q)
    out = await state.current_tools["remove_source"](
        {"kind": "rss", "value": "https://gone.example/feed"}
    )
    assert not out.get("isError")
    assert q.get_nowait() == "remove_source"
