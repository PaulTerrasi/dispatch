"""Chat-tab API: streaming chat agent + live profile read.

The PWA opens a streaming POST to `/api/chat/stream` with the full conversation
history; we run a fresh `SdkAgentRunner` with `chat_options` and forward
agent output as SSE events. The agent has the reflection toolkit, so it can
edit `profile.md` and `sources.yaml` mid-conversation; successful writes
emit a `profile_changed` SSE so the PWA refetches `GET /api/profile`.

State is fully stateless: history lives in the client and is sent in full
each turn. The Reset button is a client-side concern.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from digest.agent import RunState, SdkAgentRunner, chat_options
from digest.store_protocol import StoreProtocol
from server.deps import get_store

log = structlog.get_logger(__name__)
router = APIRouter()
StoreDep = Annotated[StoreProtocol, Depends(get_store)]

HEARTBEAT_INTERVAL_SECONDS = 10.0
AGENT_WALL_TIMEOUT_SECONDS = 240.0
# The SDK cancels its stderr reader during disconnect, so a placeholder
# "Check stderr output for details" is the only thing surfaced on a CLI
# crash unless we buffer the lines ourselves.
CLI_STDERR_BUFFER_LINES = 50
# Compact summaries of the last N SDK messages, kept so we can include them
# in the failure log when the CLI subprocess silently exits with code 1.
# 20 covers a couple of tool round-trips, enough to see what the CLI was
# doing immediately before the crash.
CLI_MESSAGE_BUFFER_ENTRIES = 20


class ChatTurn(BaseModel):
    role: str  # "user" | "assistant"
    text: str


def _format_history(history: list[ChatTurn]) -> str:
    lines: list[str] = []
    for turn in history:
        role = "User" if turn.role == "user" else "Assistant"
        lines.append(f"{role}: {turn.text}")
    return "\n".join(lines)


class ChatRequest(BaseModel):
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


TOOL_OUTPUT_MAX_CHARS = 4000


def _tool_result_text(content: Any) -> str:
    """Best-effort extraction of human-readable text from a ToolResultBlock's
    `content`, capped so we don't bloat the SSE stream with large blobs."""
    if content is None:
        return ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # List-shaped tool results are sequences of typed content blocks.
        # We only surface text blocks here — non-dict entries and dicts
        # without a string "text" field (e.g. image content) are dropped,
        # since the chat UI has no way to display them inline.
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str):
                    parts.append(t)
        text = "\n".join(parts)
    else:
        text = ""
    if len(text) > TOOL_OUTPUT_MAX_CHARS:
        text = text[:TOOL_OUTPUT_MAX_CHARS] + "\n…(truncated)"
    return text


def _summarize_message(msg: Any) -> dict[str, Any] | None:
    """Return a small dict summarising one SDK message for the crash buffer,
    or None if the message should be skipped.

    Kept compact: the goal is to see the *shape* of the last few message
    boundaries when a silent CLI crash happens, not to replay them.

    StreamEvents (one per token delta) are skipped — they'd evict every
    AssistantMessage/UserMessage in the bounded deque during any
    non-trivial text generation pass, defeating the buffer's purpose.
    """
    if isinstance(msg, StreamEvent):
        return None
    if isinstance(msg, AssistantMessage):
        blocks: list[str] = []
        for b in msg.content:
            if isinstance(b, ToolUseBlock):
                blocks.append(f"tool_use:{b.name}")
            else:
                blocks.append(type(b).__name__)
        return {"type": "AssistantMessage", "blocks": blocks}
    if isinstance(msg, UserMessage):
        content = msg.content if isinstance(msg.content, list) else []
        results: list[dict[str, Any]] = []
        for b in content:
            if isinstance(b, ToolResultBlock):
                results.append({"tool_use_id": b.tool_use_id, "is_error": bool(b.is_error)})
        return {"type": "UserMessage", "tool_results": results}
    return {"type": type(msg).__name__}


def _translate(msg: Any) -> list[bytes]:
    """Translate one SDK output item into zero or more SSE frames.

    StreamEvent → token-level `text` / `reasoning` deltas.
    AssistantMessage → `tool_start` per ToolUseBlock (we have args here).
    UserMessage → `tool_end` per ToolResultBlock (with truncated output text).
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
            elif delta.get("type") == "thinking_delta":
                text = delta.get("thinking") or ""
                if text:
                    out.append(_sse("reasoning", {"delta": text}))
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
                payload: dict[str, Any] = {
                    "tool_use_id": block.tool_use_id,
                    "ok": not bool(block.is_error),
                }
                output = _tool_result_text(block.content)
                if output:
                    payload["output"] = output
                out.append(_sse("tool_end", payload))
        return out
    return out


async def _stream_agent(store: StoreProtocol, history: list[ChatTurn]) -> AsyncIterator[bytes]:
    """Run the chat agent and yield SSE bytes.

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
    options = chat_options(state, profile_changed_q)
    cli_stderr_buffer: deque[str] = deque(maxlen=CLI_STDERR_BUFFER_LINES)

    def _capture_stderr(line: str) -> None:
        cli_stderr_buffer.append(line)
        log.warning("claude_cli.stderr", line=line)

    options.stderr = _capture_stderr
    cli_messages_buffer: deque[dict[str, Any]] = deque(maxlen=CLI_MESSAGE_BUFFER_ENTRIES)
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

    # Mutable so the closures + the error handler can share it.
    state_flags = {"result_seen": False}

    async def _drive_agent() -> None:
        async for msg in runner.run(prompt=prompt, options=options):
            summary = _summarize_message(msg)
            if summary is not None:
                cli_messages_buffer.append(summary)
            if isinstance(msg, ResultMessage) and not msg.is_error:
                # The agent has signalled turn completion. Anything the CLI
                # does after this point (including its observed habit of
                # exiting with code 1 post-completion) is irrelevant — the
                # user already got the full response.
                state_flags["result_seen"] = True
            for chunk in _translate(msg):
                await out_queue.put(chunk)
        await out_queue.put(_sse("done", {}))

    async def run_agent() -> None:
        try:
            await asyncio.wait_for(_drive_agent(), timeout=AGENT_WALL_TIMEOUT_SECONDS)
        except (
            asyncio.CancelledError
        ):  # pragma: no cover -- propagated by asyncio when client disconnects mid-stream
            raise
        except TimeoutError:
            log.error("chat.agent_timeout", timeout_s=AGENT_WALL_TIMEOUT_SECONDS)
            await out_queue.put(
                _sse(
                    "error",
                    {
                        "message": f"agent exceeded {AGENT_WALL_TIMEOUT_SECONDS:.0f}s wall time",
                        "reason": "timeout",
                    },
                )
            )
        except Exception as e:
            if state_flags["result_seen"]:
                # Known CLI bug: after the agent emits a successful
                # ResultMessage the Node CLI subprocess sometimes exits with
                # code 1, surfacing here as `Command failed with exit code 1`.
                # The conversation already completed cleanly — emit `done`
                # so the client closes the stream normally.
                log.info(
                    "chat.post_result_cli_exit_suppressed",
                    error_type=type(e).__name__,
                    error=str(e),
                )
                await out_queue.put(_sse("done", {}))
                return
            exit_code = getattr(e, "exit_code", None)
            cli_stderr = getattr(e, "stderr", None)
            buffered_stderr = "\n".join(cli_stderr_buffer) if cli_stderr_buffer else None
            recent_messages = list(cli_messages_buffer) if cli_messages_buffer else None
            log.exception(
                "chat.agent_failed",
                error_type=type(e).__name__,
                exit_code=exit_code,
                cli_stderr=cli_stderr,
                cli_stderr_buffered=buffered_stderr,
                cli_recent_messages=recent_messages,
            )
            payload: dict[str, Any] = {"message": str(e)}
            if exit_code is not None:
                payload["exit_code"] = exit_code
            if cli_stderr:
                payload["cli_stderr"] = cli_stderr
            if buffered_stderr:
                # Forwarded to the browser intentionally: single-tenant
                # personal app, the buffered lines are what makes the failure
                # actionable for the user. Revisit if this ever becomes
                # multi-tenant or the CLI starts emitting credential fragments.
                payload["cli_stderr_buffered"] = buffered_stderr
            if recent_messages:
                # Forwarded to the browser for the same single-tenant reason
                # as cli_stderr_buffered above. Summaries are shape-only
                # (tool names, block types, opaque SDK-generated tool_use_ids,
                # is_error flags) — no raw tool inputs/outputs.
                payload["cli_recent_messages"] = recent_messages
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


@router.post("/chat/stream")
async def chat_stream(store: StoreDep, body: ChatRequest) -> StreamingResponse:
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
