"""Coverage for the small branches in `digest.runner` not exercised by the
end-to-end integration tests in tests/integration/test_runner.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, ThinkingBlock

from digest.agent import RunState
from digest.runner import (
    _build_reflection_user_prompt,
    _capture,
    _events_after,
    _make_store,
    reflect_drain,
    reflect_now,
)
from digest.store import Store


def test_make_store_prefers_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both bucket and data_dir are given, S3 wins."""
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: object())
    from digest.s3_store import S3Store

    assert isinstance(_make_store(Path("/tmp/x"), "the-bucket"), S3Store)


def test_make_store_filesystem(tmp_path: Path) -> None:
    from digest.store import Store as _Store

    assert isinstance(_make_store(tmp_path, None), _Store)


def test_make_store_requires_either() -> None:
    with pytest.raises(ValueError, match="data_dir or bucket"):
        _make_store(None, None)


@pytest.mark.asyncio
async def test_capture_extracts_thinking_into_state(store: Store) -> None:
    """`_capture` should record the most recent thinking blocks for the next tool call."""
    state = RunState(store=store, today=date(2026, 5, 14))

    async def _msgs() -> AsyncIterator[Any]:
        yield AssistantMessage(
            model="x",
            content=[
                ThinkingBlock(thinking="step 1", signature="sig1"),
                ThinkingBlock(thinking="step 2", signature="sig2"),
                TextBlock(text="ignored"),
            ],
        )

    await _capture(_msgs(), state)
    assert state.pending_thinking == "step 1\n\nstep 2"


@pytest.mark.asyncio
async def test_capture_ignores_assistant_messages_without_thinking(store: Store) -> None:
    state = RunState(store=store, today=date(2026, 5, 14))

    async def _msgs() -> AsyncIterator[Any]:
        yield AssistantMessage(model="x", content=[TextBlock(text="hi")])

    await _capture(_msgs(), state)
    assert state.pending_thinking is None


def test_build_reflection_user_prompt_thumb_vs_chat() -> None:
    """Item-bearing events nudge the agent to read_triggering_curation_run; chat events
    fall back to read_recent_curation_runs."""
    thumb = _build_reflection_user_prompt(
        {"ts": "t", "kind": "thumb", "value": "up", "item_id": "abc"}
    )
    chat = _build_reflection_user_prompt({"ts": "t", "kind": "chat", "text": "hello"})
    assert "read_triggering_curation_run" in thumb
    assert "read_recent_curation_runs" in chat


def test_events_after_no_cursor_returns_all_sorted() -> None:
    """Without a cursor, every event is returned and sorted by ts ascending."""
    events = [
        {"ts": "2026-05-14T11:00:00+00:00", "kind": "thumb"},
        {"ts": "2026-05-14T10:00:00+00:00", "kind": "chat"},
    ]
    out = _events_after(events, None)
    assert [e["kind"] for e in out] == ["chat", "thumb"]


def test_events_after_filters_by_cursor() -> None:
    cursor = "2026-05-14T10:30:00+00:00"
    events = [
        {"ts": "2026-05-14T10:00:00+00:00", "kind": "before"},
        {"ts": "2026-05-14T11:00:00+00:00", "kind": "after"},
    ]
    assert [e["kind"] for e in _events_after(events, cursor)] == ["after"]


class _NoopRunner:
    async def run(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        yield {"type": "result"}


@pytest.mark.asyncio
async def test_reflect_drain_skips_events_with_non_string_ts(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An event with a non-string `ts` is malformed and must be skipped silently."""
    store = Store(tmp_data_dir)
    store.ensure_layout()

    # Plant one malformed (no `ts` key) and one normal event by stubbing reads.
    # The drain's `_events_after` helper sorts using `e.get("ts", "")`, so a
    # missing `ts` sorts first; the per-event `isinstance(ts, str)` filter
    # is what we're exercising here.
    bad = {"kind": "thumb"}  # no ts
    good = {"ts": "2026-05-14T10:00:00+00:00", "kind": "thumb", "item_id": "x"}
    monkeypatch.setattr(store, "read_recent_feedback", lambda days=30: [bad, good])  # type: ignore[arg-type]

    processed = await reflect_drain(store=store, runner=_NoopRunner())
    assert processed == 1


@pytest.mark.asyncio
async def test_reflect_now_logs_when_git_commit_fails(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure in `git_commit_all` after a reflection must not propagate
    — the run record is what matters."""
    store = Store(tmp_data_dir)
    store.ensure_layout()

    def _boom(_msg: str) -> bool:
        raise RuntimeError("git is on fire")

    monkeypatch.setattr(store, "git_commit_all", _boom)
    record = await reflect_now(
        store=store,
        triggering_event={"ts": "2026-05-14T10:00:00+00:00", "kind": "thumb", "item_id": "x"},
        runner=_NoopRunner(),
    )
    assert record is not None  # run was still persisted


@pytest.mark.asyncio
async def test_run_once_swallows_append_run_failure(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A storage error when persisting the curation run record is logged but
    must not crash the runner — the digest itself has already been written."""
    from digest.runner import run_once

    store = Store(tmp_data_dir)
    store.ensure_layout()

    real_append = store.append_run
    state = {"called": 0}

    def _flaky(record: dict[str, Any]) -> None:
        state["called"] += 1
        if state["called"] == 1:
            raise OSError("disk full")
        real_append(record)

    # Patch the actual Store the runner builds by routing _make_store to this one.
    monkeypatch.setattr("digest.runner._make_store", lambda *_a, **_k: store)
    monkeypatch.setattr(store, "append_run", _flaky)

    summary = await run_once(data_dir=tmp_data_dir, runner=_NoopRunner(), today=date(2026, 5, 14))
    assert summary.exit_reason == "no_items"


def test_main_runs_curation_when_no_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`python -m digest.runner --data-dir X` runs `run_once` and prints summary JSON."""
    import sys

    from digest import runner as runner_mod
    from digest.runner import RunSummary

    summary = RunSummary(
        run_id="abc",
        date=date(2026, 5, 14),
        item_count=2,
        started_at=datetime.now(UTC),
        duration_seconds=0.1,
        exit_reason="ok",
        profile_patches=0,
    )

    async def _fake_run(**_kwargs: Any) -> RunSummary:
        return summary

    def _drain(coro: Any) -> Any:
        try:
            coro.send(None)
        except StopIteration as stop:
            return stop.value
        raise RuntimeError("fake run_once didn't complete")

    monkeypatch.setattr(runner_mod, "run_once", _fake_run)
    monkeypatch.setattr(runner_mod.asyncio, "run", _drain)
    monkeypatch.setattr(sys, "argv", ["digest.runner", "--data-dir", str(tmp_path)])
    runner_mod.main()


def test_main_exits_nonzero_when_summary_is_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    from digest import runner as runner_mod
    from digest.runner import RunSummary

    summary = RunSummary(
        run_id="abc",
        date=date(2026, 5, 14),
        item_count=0,
        started_at=datetime.now(UTC),
        duration_seconds=0.1,
        exit_reason="error",
        profile_patches=0,
        error="boom",
    )

    async def _fake_run(**_kwargs: Any) -> RunSummary:
        return summary

    def _drain(coro: Any) -> Any:
        try:
            coro.send(None)
        except StopIteration as stop:
            return stop.value
        raise RuntimeError("fake run_once didn't complete")

    monkeypatch.setattr(runner_mod, "run_once", _fake_run)
    monkeypatch.setattr(runner_mod.asyncio, "run", _drain)
    monkeypatch.setattr(sys, "argv", ["digest.runner", "--data-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as excinfo:
        runner_mod.main()
    assert excinfo.value.code == 1


def test_main_defaults_to_local_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No --data-dir, no --bucket → defaults to `./data` (current cwd)."""
    import sys

    from digest import runner as runner_mod

    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    async def _fake_run(**kwargs: Any) -> Any:
        captured.update(kwargs)
        from digest.runner import RunSummary

        return RunSummary(
            run_id="x",
            date=date(2026, 5, 14),
            item_count=0,
            started_at=datetime.now(UTC),
            duration_seconds=0.0,
            exit_reason="no_items",
            profile_patches=0,
        )

    def _drain(coro: Any) -> Any:
        try:
            coro.send(None)
        except StopIteration as stop:
            return stop.value
        raise RuntimeError

    monkeypatch.setattr(runner_mod, "run_once", _fake_run)
    monkeypatch.setattr(runner_mod.asyncio, "run", _drain)
    monkeypatch.setattr(sys, "argv", ["digest.runner"])
    runner_mod.main()
    assert captured["data_dir"] == Path("./data")


def test_configure_logging_shim_is_idempotent() -> None:
    """The legacy `_configure_logging` shim must remain callable and idempotent."""
    from digest.runner import _configure_logging

    _configure_logging()
    _configure_logging()  # second call is a no-op


def test_runsummary_is_dataclass_with_expected_fields() -> None:
    """Belt-and-suspenders: the runner's public summary shape is part of the contract."""
    from digest.runner import RunSummary

    s = RunSummary(
        run_id="r",
        date=date(2026, 5, 14),
        item_count=1,
        started_at=datetime.now(UTC),
        duration_seconds=0.5,
        exit_reason="ok",
        profile_patches=2,
        sources_changed=1,
        error=None,
    )
    assert s.run_id == "r"
    assert s.sources_changed == 1
