"""Tests for the streaming Chat-tab endpoints.

We unit-test the SSE serializer + translator and the lock-wrapping behavior
of `build_chat_tools` directly. The full POST /api/chat/stream flow goes
through `claude_agent_sdk.query` (real subprocess), so it's exercised by
`make smoke-real` rather than here.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi.testclient import TestClient

from digest.agent import RunState, build_chat_tools
from digest.store import Store
from server.app import create_app
from server.routes_chat import _sse, _translate


@pytest.fixture
def client(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MORNING_DIGEST_DATA_DIR", str(tmp_data_dir))
    app = create_app()
    with TestClient(app) as c:
        yield c


# ── _sse / _translate ──────────────────────────────────────────────────────


def test_sse_frame_format() -> None:
    out = _sse("text", {"delta": "hi"})
    assert out == b'event: text\ndata: {"delta":"hi"}\n\n'


def test_translate_text_delta() -> None:
    msg = StreamEvent(
        uuid="u1",
        session_id="s1",
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "tok"}},
    )
    frames = _translate(msg)
    assert len(frames) == 1
    assert b"event: text" in frames[0]
    assert b'"delta":"tok"' in frames[0]


def test_translate_skips_thinking_delta() -> None:
    msg = StreamEvent(
        uuid="u1",
        session_id="s1",
        event={"type": "content_block_delta", "delta": {"type": "thinking_delta", "text": "..."}},
    )
    assert _translate(msg) == []


def test_translate_tool_use_block() -> None:
    msg = AssistantMessage(
        model="test",
        content=[
            TextBlock(text="ignored here"),
            ToolUseBlock(id="t1", name="patch_profile", input={"diff": "..."}),
        ],
    )
    frames = _translate(msg)
    assert len(frames) == 1
    assert b"event: tool_start" in frames[0]
    payload = json.loads(frames[0].decode().split("data: ", 1)[1].rstrip("\n"))
    assert payload == {"id": "t1", "name": "patch_profile", "input": {"diff": "..."}}


def test_translate_tool_result_block() -> None:
    msg = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)])
    frames = _translate(msg)
    assert len(frames) == 1
    payload = json.loads(frames[0].decode().split("data: ", 1)[1].rstrip("\n"))
    assert payload == {"tool_use_id": "t1", "ok": True}


# ── build_chat_tools: lock + profile_changed plumbing ──────────────────────


@pytest.mark.asyncio
async def test_chat_write_pushes_profile_changed(store: Store) -> None:
    state = RunState(store=store, today=datetime.now(UTC).date())
    q: asyncio.Queue[str] = asyncio.Queue()
    tools = build_chat_tools(state, q)
    by_name = {t.name: t for t in tools}

    # Seed a profile so the diff applies cleanly.
    store.write_profile("# Profile\n\n- old\n")
    diff = "--- a/profile.md\n+++ b/profile.md\n@@ -1,3 +1,3 @@\n # Profile\n \n-- old\n+- new\n"
    result = await by_name["patch_profile"].handler({"diff": diff})
    assert not result.get("isError"), result
    assert q.get_nowait() == "patch_profile"
    assert "- new" in store.read_profile()


@pytest.mark.asyncio
async def test_chat_write_blocked_by_reflection_lock(store: Store) -> None:
    state = RunState(store=store, today=datetime.now(UTC).date())
    q: asyncio.Queue[str] = asyncio.Queue()
    tools = build_chat_tools(state, q)
    by_name = {t.name: t for t in tools}

    # Reflection holds the lock externally (e.g., the hourly Fargate run).
    held = store.try_acquire_reflection_lock(ttl_seconds=900)
    assert held is not None

    store.write_profile("# Profile\n\n- old\n")
    diff = "--- a/profile.md\n+++ b/profile.md\n@@ -1,3 +1,3 @@\n # Profile\n \n-- old\n+- new\n"
    result = await by_name["patch_profile"].handler({"diff": diff})
    assert result.get("isError") is True
    # No profile_changed should fire on contention.
    assert q.empty()
    # Profile not modified.
    assert store.read_profile() == "# Profile\n\n- old\n"

    store.release_reflection_lock(held)


# ── GET /api/profile ────────────────────────────────────────────────────────


def test_profile_endpoint_returns_markdown(client: TestClient, tmp_data_dir: Path) -> None:
    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.write_profile("# Profile\n\n- woodworking\n")
    r = client.get("/api/profile")
    assert r.status_code == 200
    assert r.json() == {"markdown": "# Profile\n\n- woodworking\n"}


def test_chat_rejects_empty_history(client: TestClient) -> None:
    r = client.post("/api/chat/stream", json={"history": []})
    assert r.status_code == 400


def test_chat_requires_user_last(client: TestClient) -> None:
    r = client.post(
        "/api/chat/stream",
        json={"history": [{"role": "assistant", "text": "hi"}]},
    )
    assert r.status_code == 400
    assert r.status_code == 400


# ── _format_history ────────────────────────────────────────────────────────


def test_format_history_renders_roles() -> None:
    from server.routes_chat import ChatTurn, _format_history

    out = _format_history(
        [ChatTurn(role="user", text="hi"), ChatTurn(role="assistant", text="hello")]
    )
    assert out == "User: hi\nAssistant: hello"


def test_format_history_treats_unknown_role_as_assistant() -> None:
    from server.routes_chat import ChatTurn, _format_history

    out = _format_history([ChatTurn(role="system", text="anything")])
    assert out == "Assistant: anything"


# ── _translate fallthrough branches ────────────────────────────────────────


def test_translate_ignores_unknown_stream_event_type() -> None:
    """Stream events that aren't text deltas (e.g. message_start) produce no frames."""
    msg = StreamEvent(uuid="u", session_id="s", event={"type": "message_start"})
    assert _translate(msg) == []


def test_translate_ignores_text_delta_with_empty_string() -> None:
    msg = StreamEvent(
        uuid="u",
        session_id="s",
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": ""}},
    )
    assert _translate(msg) == []


def test_translate_assistant_message_with_only_text_block_yields_nothing() -> None:
    """The translator emits frames only for ToolUseBlocks; TextBlocks come via deltas."""
    msg = AssistantMessage(model="x", content=[TextBlock(text="hello")])
    assert _translate(msg) == []


def test_translate_user_message_non_list_content_yields_nothing() -> None:
    """A UserMessage whose content is a plain string is the SDK's normal turn — skip."""
    msg = UserMessage(content="just plain text")
    assert _translate(msg) == []


def test_translate_user_message_with_non_tool_result_block() -> None:
    """Blocks inside a UserMessage that aren't ToolResultBlock are ignored."""
    msg = UserMessage(content=[TextBlock(text="hi")])
    assert _translate(msg) == []


def test_translate_drops_other_message_types() -> None:
    """Anything not StreamEvent/AssistantMessage/UserMessage is dropped silently."""
    assert _translate({"type": "result"}) == []


# ── full /api/chat/stream SSE flow ───────────────────────────────────────────


class _FakeRunner:
    """Yields a fixed sequence the same way the real SDK runner would."""

    def __init__(self, messages: list) -> None:
        self._messages = messages

    async def run(self, prompt, options):
        for m in self._messages:
            yield m


def test_talk_streams_translated_frames_then_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the SSE generator with a fake runner so we can assert the full
    frame sequence: tool_start → tool_end → text → done."""
    import server.routes_chat as rt

    fake = _FakeRunner(
        [
            AssistantMessage(
                model="x", content=[ToolUseBlock(id="t1", name="read_profile", input={})]
            ),
            UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)]),
            StreamEvent(
                uuid="u",
                session_id="s",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Hi!"},
                },
            ),
        ]
    )
    monkeypatch.setattr(rt, "SdkAgentRunner", lambda: fake)

    body = b""
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"history": [{"role": "user", "text": "hello"}]},
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for chunk in r.iter_bytes():
            body += chunk
    assert b"event: tool_start" in body
    assert b"event: tool_end" in body
    assert b"event: text" in body
    assert b"event: done" in body


def test_talk_wipes_claude_config_dir_before_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On every invocation we wipe the CLI's per-container state to avoid
    warm-Lambda session-file contamination."""
    import server.routes_chat as rt

    cfg = tmp_path / "claude-cfg"
    cfg.mkdir()
    (cfg / "session.json").write_text("stale", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))

    fake = _FakeRunner([])
    monkeypatch.setattr(rt, "SdkAgentRunner", lambda: fake)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"history": [{"role": "user", "text": "hi"}]},
    ) as r:
        for _ in r.iter_bytes():
            pass
    assert not cfg.exists()


def test_talk_emits_profile_changed_event_on_successful_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_data_dir: Path
) -> None:
    """When a wrapped write-tool fires successfully, the SSE stream must
    surface a `profile_changed` event."""
    import server.routes_chat as rt

    store = Store(tmp_data_dir)
    store.ensure_layout()

    captured_q: dict[str, asyncio.Queue[str]] = {}
    real_chat_options = rt.chat_options

    def _spy_chat_options(state, q, **kwargs):
        captured_q["q"] = q
        return real_chat_options(state, q, **kwargs)

    monkeypatch.setattr(rt, "chat_options", _spy_chat_options)

    class _PushRunner:
        async def run(self, prompt, options):
            del prompt, options
            # Simulate the write-tool wrapper completing successfully by
            # pushing onto the queue the same way the wrapper does.
            captured_q["q"].put_nowait("add_source")
            # Yield a no-op message so the generator drains.
            await asyncio.sleep(0)
            return
            yield  # pragma: no cover -- makes this an async generator

    monkeypatch.setattr(rt, "SdkAgentRunner", lambda: _PushRunner())

    body = b""
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"history": [{"role": "user", "text": "add foo"}]},
    ) as r:
        for chunk in r.iter_bytes():
            body += chunk
    assert b"event: profile_changed" in body
    assert b'"by":"add_source"' in body
    assert b"event: done" in body


def test_talk_emits_heartbeat_when_agent_is_slow(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow agent should still produce keepalive `heartbeat` frames so the
    proxy doesn't time the connection out."""
    import server.routes_chat as rt

    monkeypatch.setattr(rt, "HEARTBEAT_INTERVAL_SECONDS", 0.02)

    class _SlowRunner:
        async def run(self, prompt, options):
            del prompt, options
            await asyncio.sleep(0.1)  # idle long enough for heartbeats to fire
            return
            yield  # pragma: no cover

    monkeypatch.setattr(rt, "SdkAgentRunner", lambda: _SlowRunner())

    body = b""
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"history": [{"role": "user", "text": "hi"}]},
    ) as r:
        for chunk in r.iter_bytes():
            body += chunk
    assert b"event: heartbeat" in body


def test_talk_emits_error_frame_on_agent_timeout(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the agent exceeds the wall-time, the stream surfaces an `error` SSE."""
    import server.routes_chat as rt

    # Tighten the timeout so the test isn't slow.
    monkeypatch.setattr(rt, "AGENT_WALL_TIMEOUT_SECONDS", 0.05)

    class _HangRunner:
        async def run(self, prompt, options):
            import asyncio

            await asyncio.sleep(2.0)  # exceeds the 0.05s wall time
            return
            yield  # pragma: no cover

    monkeypatch.setattr(rt, "SdkAgentRunner", lambda: _HangRunner())

    body = b""
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"history": [{"role": "user", "text": "hi"}]},
    ) as r:
        for chunk in r.iter_bytes():
            body += chunk
    assert b"event: error" in body
    assert b"timeout" in body
