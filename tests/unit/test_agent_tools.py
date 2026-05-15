"""Tests for the curation- and reflection-phase SDK tools wired up in
`digest.agent`.

These exercise both the happy path and the catch-all error branches that
surface fetcher failures as `isError: True` tool responses, plus the small
helpers (`_compact_curation_runs`, `_stable_id`, talk-tool lock wrapping).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
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
async def test_curation_read_recent_feedback_renders_events(store: Store) -> None:
    state = _state(store)
    store.append_feedback({"kind": "thumb", "value": "up", "item_id": "a"})
    build_curation_tools(state)
    out = await state.current_tools["read_recent_feedback"]({"days": 14})
    text = out["content"][0]["text"]
    assert '"kind": "thumb"' in text
    assert text != "(no feedback yet)"


@pytest.mark.asyncio
async def test_curation_read_recent_feedback_empty(store: Store) -> None:
    state = _state(store)
    build_curation_tools(state)
    out = await state.current_tools["read_recent_feedback"]({})
    assert out["content"][0]["text"] == "(no feedback yet)"


@pytest.mark.asyncio
async def test_curation_read_recent_digests_empty_and_populated(store: Store) -> None:
    state = _state(store)
    build_curation_tools(state)

    # Empty case
    out = await state.current_tools["read_recent_digests"]({"days": 7})
    assert out["content"][0]["text"] == "(none)"

    today = datetime.now(UTC).date()
    store.write_digest(
        today, [{"id": "1", "title": "T", "source": "s.com", "url": "https://s.com/x"}], ""
    )
    out = await state.current_tools["read_recent_digests"]({"days": 7})
    assert "T\ts.com" in out["content"][0]["text"]


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
async def test_reflection_patch_profile_rejects_bad_diff(store: Store) -> None:
    state = _state(store)
    build_reflection_tools(state)
    out = await state.current_tools["patch_profile"]({"diff": "garbage"})
    assert out.get("isError") is True
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
    opts = curation_options(state)
    assert state.curation_system_prompt
    assert opts.system_prompt == state.curation_system_prompt
    assert "mcp__digest__submit_digest" in opts.allowed_tools


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
    # talk also has a stderr forwarder.
    assert callable(opts.stderr)


def test_chat_options_stderr_forwarder_invokes_logger(store: Store) -> None:
    """The stderr callback must invoke the structlog logger so SDK errors aren't
    silent. We spy on the module-level `log.warning` to avoid coupling to the
    structlog<->stdlib bridge implementation."""
    from digest import agent as agent_mod

    state = _state(store)
    q: asyncio.Queue[str] = asyncio.Queue()
    opts = chat_options(state, q)
    calls: list[tuple[str, dict[str, Any]]] = []

    def _spy_warning(event: str, **kwargs: Any) -> None:
        calls.append((event, kwargs))

    original = agent_mod.log.warning
    agent_mod.log.warning = _spy_warning  # type: ignore[method-assign]
    try:
        assert opts.stderr is not None
        opts.stderr("CLI: something happened")
    finally:
        agent_mod.log.warning = original  # type: ignore[method-assign]

    assert calls == [("claude_cli.stderr", {"line": "CLI: something happened"})]


# ── Chat tools: lock wrapping for the write toolkit ────────────────────────


@pytest.mark.asyncio
async def test_talk_tools_read_only_methods_are_unwrapped(store: Store) -> None:
    """Only patch_profile/add_source/remove_source are wrapped; reads are passthrough."""
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
async def test_read_triggering_curation_run_scans_legacy_tool_log(store: Store) -> None:
    """When `submitted_item_ids` is missing, the tool falls back to scanning
    `tool_log` entries for a `submit_digest` call. Per the implementation note
    this is best-effort and currently doesn't extract ids — so even with a
    submit_digest entry present, the scan completes without a match."""
    store.append_run(
        {
            "run_id": "old",
            "kind": "curation",
            "started_at": "2026-05-14T08:00:00+00:00",
            "tool_log": [
                {"tool": "fetch_rss", "args": {"url": "u"}, "outcome": "ok"},
                {"tool": "submit_digest", "args": {"count": 1}, "outcome": "ok"},
            ],
        }
    )
    state = _state(store)
    state.triggering_event = {
        "ts": "2026-05-14T10:00:00+00:00",
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
