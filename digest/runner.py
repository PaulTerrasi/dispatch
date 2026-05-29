"""Daily run orchestration: curation -> persistence -> reflection -> commit."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog
from claude_agent_sdk import AssistantMessage, ThinkingBlock

from digest.agent import (
    AgentRunner,
    RunState,
    SdkAgentRunner,
    curation_options,
    reflection_options,
)
from digest.log import configure_logging
from digest.s3_store import S3Store
from digest.store import Store
from digest.store_protocol import StoreProtocol

log = structlog.get_logger()

_CURATION_PROMPT = "Begin morning curation."


@dataclass
class RunSummary:
    run_id: str
    date: date
    item_count: int
    started_at: datetime
    duration_seconds: float
    exit_reason: str  # "ok" | "no_items" | "error"
    profile_patches: int
    sources_changed: int = 0
    error: str | None = None


def _configure_logging() -> None:
    """Backwards-compatible shim — prefer `digest.log.configure_logging`."""
    configure_logging()


async def _capture(messages: AsyncIterator[Any], state: RunState) -> None:
    """Consume an agent message stream, extracting thinking blocks into state.

    Thinking text from each AssistantMessage is stored in state.pending_thinking
    so RunState.record() can attach it to the tool call that immediately follows.
    """
    async for msg in messages:
        if isinstance(msg, AssistantMessage):
            thoughts = [b.thinking for b in msg.content if isinstance(b, ThinkingBlock)]
            if thoughts:
                state.pending_thinking = "\n\n".join(thoughts)


def _make_store(data_dir: Path | None, bucket: str | None) -> StoreProtocol:
    if bucket:
        return S3Store(bucket)
    if data_dir:
        return Store(data_dir)
    raise ValueError("Either data_dir or bucket must be provided")


def _build_reflection_user_prompt(event: dict[str, Any]) -> str:
    has_item = bool(event.get("item_id"))
    trace_hint = (
        "read_triggering_curation_run to see the run (and profile snapshot) that produced this item"
        if has_item
        else "read_recent_curation_runs to see how curation has been behaving"
    )
    return (
        "A new feedback event just arrived — this is the ONE event you're "
        "responding to. Do not act on later events you may see in "
        "read_recent_feedback.\n"
        f"```json\n{json.dumps(event, indent=2)}\n```\n"
        "Start with read_reflection_memory, then use "
        f"{trace_hint}, then read_recent_feedback for prior pattern context."
    )


def _build_run_record(
    *,
    kind: str,
    state: RunState,
    started: datetime,
    run_id: str,
    user_prompt: str,
    system_prompt: str,
    item_count: int | None = None,
    triggering_feedback: dict[str, Any] | None = None,
    exit_reason: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "run_id": run_id,
        "kind": kind,
        "started_at": started.isoformat(),
        "duration_seconds": (datetime.now(UTC) - started).total_seconds(),
        "tool_calls": len(state.tool_log),
        "tool_log": state.tool_log,
        "profile_patches": state.profile_patches_applied,
        "sources_changed": state.sources_changed,
        "reflection_notes": state.reflection_notes,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    if item_count is not None:
        record["item_count"] = item_count
    if kind == "curation":
        record["submitted_item_ids"] = [
            i.get("id") for i in (state.submitted_items or []) if i.get("id")
        ]
        record["profile_snapshot"] = state.profile_snapshot
    if triggering_feedback is not None:
        record["triggering_feedback"] = triggering_feedback
    if exit_reason is not None:
        record["exit_reason"] = exit_reason
    if error is not None:
        record["error"] = error
    return record


async def reflect_now(
    *,
    store: StoreProtocol,
    triggering_event: dict[str, Any],
    runner: AgentRunner | None = None,
) -> dict[str, Any] | None:
    """Standalone reflection pass — focused on a single triggering feedback event.

    Returns the persisted run record, or None on failure.
    """
    today = datetime.now(UTC).date()
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(UTC)
    state = RunState(store=store, today=today, run_id=run_id)
    state.triggering_event = triggering_event
    agent: AgentRunner = runner if runner is not None else SdkAgentRunner()
    user_prompt = _build_reflection_user_prompt(triggering_event)
    error_msg: str | None = None
    try:
        ropts = reflection_options(state)
        await _capture(agent.run(prompt=user_prompt, options=ropts), state)
    except Exception as e:
        log.exception("reflection.background_failed", run_id=run_id)
        error_msg = f"{type(e).__name__}: {e}" or type(e).__name__

    record = _build_run_record(
        kind="reflection",
        state=state,
        started=started,
        run_id=run_id,
        user_prompt=user_prompt,
        system_prompt=state.reflection_system_prompt,
        triggering_feedback=triggering_event,
        exit_reason="error" if error_msg is not None else None,
        error=error_msg,
    )
    try:
        store.append_run(record)
    except Exception:
        log.exception("reflection.append_run_failed", run_id=run_id)
        return None

    msg = (
        f"reflection {datetime.now(UTC).isoformat()[:16]} "
        f"({state.profile_patches_applied} profile edits, "
        f"{state.sources_changed} source changes)"
    )
    try:
        store.git_commit_all(msg)
    except Exception:
        log.exception("reflection.git_commit_failed", run_id=run_id)
    return record


def _events_after(events: list[dict[str, Any]], cursor_ts: str | None) -> list[dict[str, Any]]:
    if cursor_ts is None:
        return sorted(events, key=lambda e: e.get("ts", ""))
    return sorted(
        [e for e in events if (e.get("ts") or "") > cursor_ts],
        key=lambda e: e.get("ts", ""),
    )


async def reflect_drain(
    *,
    store: StoreProtocol,
    runner: AgentRunner | None = None,
    lock_ttl_seconds: int = 900,
) -> int:
    """Drain pending feedback events into reflection runs, single-flight.

    Returns the number of reflection runs executed (0 if the lock was held).
    """
    token = store.try_acquire_reflection_lock(ttl_seconds=lock_ttl_seconds)
    if token is None:
        log.info("reflection.lock_held")
        return 0
    processed = 0
    aborted = False
    try:
        agent = runner if runner is not None else SdkAgentRunner()
        while not aborted:
            cursor = store.read_reflection_cursor()
            # Read enough history to never miss an event (drain after long lock holds).
            events = _events_after(store.read_recent_feedback(days=30), cursor)
            if not events:
                break
            for event in events:
                ts = event.get("ts")
                if not isinstance(ts, str):
                    continue
                log.info("reflection.processing_event", ts=ts)
                record = await reflect_now(store=store, triggering_event=event, runner=agent)
                if record is None:
                    # Persistence failed — leave cursor untouched so this
                    # event is retried on the next drain.
                    log.warning("reflection.drain_aborted", ts=ts)
                    aborted = True
                    break
                store.write_reflection_cursor(ts)
                processed += 1
    finally:
        store.release_reflection_lock(token)
    log.info("reflection.drain_done", processed=processed, aborted=aborted)
    return processed


async def _run_curation(agent: AgentRunner, state: RunState) -> None:
    """Curation phase: agent populates state.submitted_items via tool calls."""
    try:
        opts = curation_options(state)
        await _capture(agent.run(prompt=_CURATION_PROMPT, options=opts), state)
    finally:
        state.cleanup_temp_dirs()


async def run_once(
    *,
    data_dir: Path | None = None,
    bucket: str | None = None,
    runner: AgentRunner | None = None,
    today: date | None = None,
) -> RunSummary:
    agent: AgentRunner = runner if runner is not None else SdkAgentRunner()
    today = today or datetime.now(UTC).date()
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(UTC)
    log.info("run.start", run_id=run_id, date=today.isoformat())

    store = _make_store(data_dir, bucket)
    store.ensure_layout()
    store.git_init_if_needed()
    state = RunState(store=store, today=today, run_id=run_id)

    try:
        await _run_curation(agent, state)
    except Exception as e:  # pragma: no cover - exercised by integration test
        log.exception("run.curation_failed", run_id=run_id)
        return RunSummary(
            run_id=run_id,
            date=today,
            item_count=0,
            started_at=started,
            duration_seconds=(datetime.now(UTC) - started).total_seconds(),
            exit_reason="error",
            profile_patches=0,
            error=str(e),
        )

    items = state.submitted_items if state.submitted_items is not None else []
    store.write_digest(today, items, state.agent_notes)
    log.info("run.digest_written", run_id=run_id, count=len(items))

    record = _build_run_record(
        kind="curation",
        state=state,
        started=started,
        run_id=run_id,
        user_prompt=_CURATION_PROMPT,
        system_prompt=state.curation_system_prompt,
        item_count=len(items),
    )
    try:
        store.append_run(record)
    except Exception:
        log.exception("run.append_run_failed", run_id=run_id)

    msg = (
        f"digest {today.isoformat()} "
        f"({len(items)} items, {state.profile_patches_applied} profile edits, "
        f"{state.sources_changed} source changes)"
    )
    try:
        store.git_commit_all(msg)
    except Exception:  # pragma: no cover
        log.exception("run.git_commit_failed", run_id=run_id)

    summary = RunSummary(
        run_id=run_id,
        date=today,
        item_count=len(items),
        started_at=started,
        duration_seconds=(datetime.now(UTC) - started).total_seconds(),
        exit_reason="ok" if items else "no_items",
        profile_patches=state.profile_patches_applied,
        sources_changed=state.sources_changed,
    )
    log.info("run.done", **summary.__dict__)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(prog="digest.runner")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--bucket", default=None, help="S3 bucket name (overrides --data-dir)")
    parser.add_argument(
        "--reflect-drain",
        action="store_true",
        help="Drain pending feedback events into per-event reflections (single-flight).",
    )
    args = parser.parse_args()

    if args.bucket is None and args.data_dir is None:
        args.data_dir = Path("./data")

    _configure_logging()
    # Resolve SSM-path env vars (e.g. CLAUDE_CODE_OAUTH_TOKEN) before any
    # agent code path reads them. No-op when values are literal (local dev).
    from server.config import resolve_ssm_env_vars

    resolve_ssm_env_vars()
    if args.reflect_drain:
        store = _make_store(args.data_dir, args.bucket)
        store.ensure_layout()
        store.git_init_if_needed()
        asyncio.run(reflect_drain(store=store))
        return

    summary = asyncio.run(
        run_once(
            data_dir=args.data_dir,
            bucket=args.bucket,
        )
    )
    print(json.dumps(summary.__dict__, default=str, indent=2))
    if summary.exit_reason == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
