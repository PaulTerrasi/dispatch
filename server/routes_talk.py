"""Talk-tab API: streaming chat agent + live profile read.

The PWA opens a streaming POST to `/api/chat/talk` with the full conversation
history; we run a fresh `SdkAgentRunner` with `talk_options` and forward
agent output as SSE events. The agent has the reflection toolkit, so it can
edit `profile.md` and `sources.yaml` mid-conversation; successful writes
emit a `profile_changed` SSE so the PWA refetches `GET /api/profile`.

State is fully stateless: history lives in the client and is sent in full
each turn, just like `/api/onboarding/message`. The Reset button is a
client-side concern.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from claude_agent_sdk import (
    AssistantMessage,
    StreamEvent,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from digest.agent import RunState, SdkAgentRunner, talk_options
from digest.store_protocol import StoreProtocol
from server.deps import get_store
from server.routes_onboarding import ChatTurn, _format_history

log = structlog.get_logger(__name__)
router = APIRouter()
StoreDep = Annotated[StoreProtocol, Depends(get_store)]

HEARTBEAT_INTERVAL_SECONDS = 10.0
AGENT_WALL_TIMEOUT_SECONDS = 240.0


class TalkRequest(BaseModel):
    history: list[ChatTurn]


class ProfileMarkdown(BaseModel):
    markdown: str


@router.get("/profile", response_model=ProfileMarkdown)
def read_profile_markdown(store: StoreDep) -> ProfileMarkdown:
    """Return the current `profile.md` content for the live profile view."""
    return ProfileMarkdown(markdown=store.read_profile())


def _sse(event: str, data: dict[str, Any]) -> bytes:
    """Serialize one SSE frame. Each frame ends with \\n\\n per the spec."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def _translate(msg: Any) -> list[bytes]:
    """Translate one SDK output item into zero or more SSE frames.

    StreamEvent → token-level `text` deltas (skipping thinking deltas).
    AssistantMessage → `tool_start` per ToolUseBlock (we have args here).
    UserMessage → `tool_end` per ToolResultBlock.
    Other message types are dropped — the SSE generator emits `done` itself.
    """
    out: list[bytes] = []
    if isinstance(msg, StreamEvent):
        ev = msg.event or {}
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text") or ""
                if text:
                    out.append(_sse("text", {"delta": text}))
        return out
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                out.append(
                    _sse(
                        "tool_start",
                        {"id": block.id, "name": block.name, "input": block.input},
                    )
                )
        return out
    if isinstance(msg, UserMessage):
        # UserMessage with ToolResultBlock content is the SDK's way of feeding
        # tool results back into the conversation. Surface as tool_end.
        content = msg.content if isinstance(msg.content, list) else []
        for block in content:
            if isinstance(block, ToolResultBlock):
                out.append(
                    _sse(
                        "tool_end",
                        {
                            "tool_use_id": block.tool_use_id,
                            "ok": not bool(block.is_error),
                        },
                    )
                )
        return out
    return out


async def _stream_agent(store: StoreProtocol, history: list[ChatTurn]) -> AsyncIterator[bytes]:
    """Run the talk agent and yield SSE bytes.

    Uses a single `out_queue` as the merge point for three concurrent
    producers: the agent loop, a 10s heartbeat, and the profile-changed
    drain. Yields until the agent finishes or errors, then cancels the
    helpers.
    """
    # Wipe the bundled CLI's per-container state. /tmp persists across warm
    # Lambda invocations; leftover session files from a prior (possibly
    # cancelled) request are a likely cause of silent CLI exits with code 1.
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        shutil.rmtree(config_dir, ignore_errors=True)

    state = RunState(store=store, today=datetime.now(UTC).date())
    profile_changed_q: asyncio.Queue[str] = asyncio.Queue()
    options = talk_options(state, profile_changed_q)
    runner = SdkAgentRunner()

    prompt = (
        "Here is the conversation so far. Produce the next assistant message. "
        "If the user asked you to change the profile or sources, edit them now. "
        "Always end with `end_reflection` summarising what you did.\n\n" + _format_history(history)
    )

    out_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                await out_queue.put(_sse("heartbeat", {}))
        except asyncio.CancelledError:
            pass

    async def drain_profile_changed() -> None:
        try:
            while True:
                tool_name = await profile_changed_q.get()
                await out_queue.put(_sse("profile_changed", {"by": tool_name}))
        except asyncio.CancelledError:
            pass

    async def _drive_agent() -> None:
        async for msg in runner.run(prompt=prompt, options=options):
            for chunk in _translate(msg):
                await out_queue.put(chunk)
        await out_queue.put(_sse("done", {}))

    async def run_agent() -> None:
        try:
            await asyncio.wait_for(_drive_agent(), timeout=AGENT_WALL_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            log.error("talk.agent_timeout", timeout_s=AGENT_WALL_TIMEOUT_SECONDS)
            await out_queue.put(
                _sse(
                    "error",
                    {
                        "message": f"agent exceeded {AGENT_WALL_TIMEOUT_SECONDS:.0f}s wall time",
                        "reason": "timeout",
                    },
                )
            )
        except Exception as e:  # pragma: no cover — surfaced to client
            exit_code = getattr(e, "exit_code", None)
            cli_stderr = getattr(e, "stderr", None)
            log.exception(
                "talk.agent_failed",
                error_type=type(e).__name__,
                exit_code=exit_code,
                cli_stderr=cli_stderr,
            )
            payload: dict[str, Any] = {"message": str(e)}
            if exit_code is not None:
                payload["exit_code"] = exit_code
            if cli_stderr:
                payload["cli_stderr"] = cli_stderr
            await out_queue.put(_sse("error", payload))
        finally:
            await out_queue.put(None)

    hb_task = asyncio.create_task(heartbeat())
    pc_task = asyncio.create_task(drain_profile_changed())
    agent_task = asyncio.create_task(run_agent())

    try:
        while True:
            chunk = await out_queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        for t in (hb_task, pc_task, agent_task):
            t.cancel()
        # Allow cancellations to settle so we don't leak warnings.
        await asyncio.gather(hb_task, pc_task, agent_task, return_exceptions=True)


@router.post("/chat/talk")
async def chat_talk(store: StoreDep, body: TalkRequest) -> StreamingResponse:
    if not body.history:
        raise HTTPException(status_code=400, detail="history must be non-empty")
    if body.history[-1].role != "user":
        raise HTTPException(status_code=400, detail="last turn must be from user")
    return StreamingResponse(
        _stream_agent(store, body.history),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
