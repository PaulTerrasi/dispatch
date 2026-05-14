"""Tests for the streaming Talk-tab endpoints.

We unit-test the SSE serializer + translator and the lock-wrapping behavior
of `build_talk_tools` directly. The full POST /api/chat/talk flow goes
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

from digest.agent import RunState, build_talk_tools
from digest.store import Store
from server.app import create_app
from server.routes_talk import _sse, _translate


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


# ── build_talk_tools: lock + profile_changed plumbing ──────────────────────


@pytest.mark.asyncio
async def test_talk_write_pushes_profile_changed(store: Store) -> None:
    state = RunState(store=store, today=datetime.now(UTC).date())
    q: asyncio.Queue[str] = asyncio.Queue()
    tools = build_talk_tools(state, q)
    by_name = {t.name: t for t in tools}

    # Seed a profile so the diff applies cleanly.
    store.write_profile("# Profile\n\n- old\n")
    diff = "--- a/profile.md\n+++ b/profile.md\n@@ -1,3 +1,3 @@\n # Profile\n \n-- old\n+- new\n"
    result = await by_name["patch_profile"].handler({"diff": diff})
    assert not result.get("isError"), result
    assert q.get_nowait() == "patch_profile"
    assert "- new" in store.read_profile()


@pytest.mark.asyncio
async def test_talk_write_blocked_by_reflection_lock(store: Store) -> None:
    state = RunState(store=store, today=datetime.now(UTC).date())
    q: asyncio.Queue[str] = asyncio.Queue()
    tools = build_talk_tools(state, q)
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


def test_talk_rejects_empty_history(client: TestClient) -> None:
    r = client.post("/api/chat/talk", json={"history": []})
    assert r.status_code == 400


def test_talk_requires_user_last(client: TestClient) -> None:
    r = client.post(
        "/api/chat/talk",
        json={"history": [{"role": "assistant", "text": "hi"}]},
    )
    assert r.status_code == 400
