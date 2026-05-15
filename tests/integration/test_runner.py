"""End-to-end test for digest.runner using a ScriptedAgentRunner.

This is the seam called out in the plan: tests never make a real Claude call.
We instead drive our tools in a deterministic order and assert on side effects.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from digest.agent import RunState, ScriptedAgentRunner
from digest.runner import run_once
from digest.store import Store


class TwoPhaseScriptedRunner:
    """Runs two scripts back-to-back: curation then reflection.

    Reads `RunState.current_tools` (populated as a side effect of
    `curation_options`/`reflection_options`) lazily via a get_state callback,
    so the runner can be constructed before run_once builds the state.
    """

    def __init__(
        self,
        get_state: Callable[[], RunState | None],
        curation: list[tuple[str, dict[str, Any]]],
        reflection: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self._get_state = get_state
        self._scripts = [curation, reflection]
        self._idx = 0

    async def run(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        state = self._get_state()
        assert state is not None, "RunState was not captured before runner.run()"
        script = self._scripts[self._idx]
        self._idx += 1
        async for msg in ScriptedAgentRunner(state, script).run(prompt, options):
            yield msg


@pytest.fixture
def captured_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, RunState | None]:
    """Captures the RunState built inside run_once via a __init__ spy."""
    captured: dict[str, RunState | None] = {"state": None}
    original_init = RunState.__init__

    def _spy(self: RunState, *args: Any, **kwargs: Any) -> None:  # type: ignore[no-untyped-def]
        original_init(self, *args, **kwargs)
        captured["state"] = self

    monkeypatch.setattr(RunState, "__init__", _spy)
    return captured


@pytest.mark.asyncio
async def test_runner_writes_digest_and_commits(
    tmp_data_dir: Path, captured_state: dict[str, RunState | None]
):
    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.sources_path.write_text(
        "rss:\n  - url: https://example.com/feed\n    tags: [llm]\n",
        encoding="utf-8",
    )

    curation = [
        ("list_sources", {}),
        (
            "submit_digest",
            {
                "items": [
                    {
                        "type": "article",
                        "title": "Why agents need evals",
                        "source": "Example",
                        "url": "https://example.com/post-1",
                        "summary": "Sober look at LLM agent evals.",
                    }
                ],
                "agent_notes": "One keeper from a quiet feed today.",
            },
        ),
    ]
    reflection = [("end_reflection", {"notes": "profile is fine."})]

    runner = TwoPhaseScriptedRunner(
        get_state=lambda: captured_state["state"],
        curation=curation,
        reflection=reflection,
    )
    summary = await run_once(data_dir=tmp_data_dir, runner=runner, today=date(2026, 4, 29))

    assert summary.exit_reason == "ok"
    assert summary.item_count == 1

    digest = store.read_digest(date(2026, 4, 29))
    assert digest is not None
    assert digest["items"][0]["title"] == "Why agents need evals"
    assert digest["agent_notes"].startswith("One keeper")
    assert digest["items"][0]["id"]
    assert digest["items"][0]["run_id"] == summary.run_id

    runs = store.read_recent_runs(days=1)
    assert len(runs) == 1
    assert runs[0]["run_id"] == summary.run_id
    assert runs[0]["kind"] == "curation"
    assert runs[0]["item_count"] == 1
    # Current schema: prompt fields are flat, tool_log is a list.
    assert "system_prompt" in runs[0]
    assert "user_prompt" in runs[0]
    assert isinstance(runs[0].get("tool_log"), list)

    assert (tmp_data_dir / ".git").exists()


@pytest.mark.asyncio
async def test_quiet_morning_writes_empty_digest(
    tmp_data_dir: Path, captured_state: dict[str, RunState | None]
):
    """If the agent never calls submit_digest, we still write an empty digest."""
    store = Store(tmp_data_dir)
    store.ensure_layout()

    runner = TwoPhaseScriptedRunner(
        get_state=lambda: captured_state["state"],
        curation=[("list_sources", {})],
        reflection=[("end_reflection", {"notes": "everything was a rehash today."})],
    )
    summary = await run_once(data_dir=tmp_data_dir, runner=runner, today=date(2026, 4, 29))
    assert summary.exit_reason == "no_items"
    assert summary.item_count == 0
    digest = store.read_digest(date(2026, 4, 29))
    assert digest is not None
    assert digest["items"] == []
    # Curation run is still recorded in the runs store, even with zero items.
    runs = store.read_recent_runs(days=1)
    assert len(runs) == 1
    assert runs[0]["kind"] == "curation"
    assert runs[0]["run_id"] == summary.run_id


class _ReflectScriptedRunner:
    """Drives reflect_now with a fixed script, reading state lazily."""

    def __init__(
        self,
        get_state: Callable[[], RunState | None],
        script: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self._get_state = get_state
        self._script = script

    async def run(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        state = self._get_state()
        assert state is not None
        async for msg in ScriptedAgentRunner(state, self._script).run(prompt, options):
            yield msg


@pytest.mark.asyncio
async def test_reflection_can_edit_profile(
    tmp_data_dir: Path, captured_state: dict[str, RunState | None]
):
    """Reflection contract: feedback like 'more woodworking' should be
    actionable via patch_profile."""
    from digest.runner import reflect_now

    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.write_profile(
        "# Profile\n\n"
        "## Standing interests\n"
        "- LLMs\n\n"
        "## Things I'm currently exploring\n"
        "-\n\n"
        "## Things I've explicitly said I want LESS of\n"
        "-\n\n"
        "## Voice / taste notes\n"
        "-\n"
    )
    event = {
        "ts": "2026-04-29T12:00:00+00:00",
        "kind": "chat",
        "text": "more woodworking, less AI hot takes",
    }
    store.append_feedback(event)

    diff = (
        "@@ -3,2 +3,3 @@\n ## Standing interests\n - LLMs\n+- Woodworking — beginner, hand tools\n"
    )
    runner = _ReflectScriptedRunner(
        get_state=lambda: captured_state["state"],
        script=[
            ("read_profile", {}),
            ("read_recent_feedback", {"days": 14}),
            ("patch_profile", {"diff": diff}),
            ("end_reflection", {"notes": "added woodworking interest per chat feedback."}),
        ],
    )
    record = await reflect_now(store=store, triggering_event=event, runner=runner)
    assert record is not None
    assert record["profile_patches"] == 1
    assert "Woodworking" in store.read_profile()


@pytest.mark.asyncio
async def test_reflection_can_add_source(
    tmp_data_dir: Path, captured_state: dict[str, RunState | None]
):
    """Reflection contract: add_source writes a new entry to sources.yaml."""
    from digest.runner import reflect_now

    store = Store(tmp_data_dir)
    store.ensure_layout()

    event = {"ts": "2026-04-29T12:00:00+00:00", "kind": "chat", "text": "add Simon Willison"}
    runner = _ReflectScriptedRunner(
        get_state=lambda: captured_state["state"],
        script=[
            ("list_sources", {}),
            (
                "add_source",
                {
                    "kind": "rss",
                    "value": "https://simonwillison.net/atom/everything/",
                    "name": "Simon Willison",
                    "tags": ["llm", "tooling"],
                },
            ),
            ("end_reflection", {"notes": "added Simon Willison feed."}),
        ],
    )
    record = await reflect_now(store=store, triggering_event=event, runner=runner)

    sources = store.list_sources()
    assert any(
        s.kind == "rss" and s.value == "https://simonwillison.net/atom/everything/" for s in sources
    )
    assert record is not None
    assert record["sources_changed"] == 1


@pytest.mark.asyncio
async def test_reflection_can_remove_source(
    tmp_data_dir: Path, captured_state: dict[str, RunState | None]
):
    """Reflection contract: remove_source deletes an entry from sources.yaml."""
    from digest.runner import reflect_now

    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.sources_path.write_text(
        "rss:\n  - url: https://example.com/feed\n    tags: [llm]\nyoutube: []\nsites: []\n",
        encoding="utf-8",
    )

    event = {"ts": "2026-04-29T12:00:00+00:00", "kind": "chat", "text": "drop the example feed"}
    runner = _ReflectScriptedRunner(
        get_state=lambda: captured_state["state"],
        script=[
            ("list_sources", {}),
            ("remove_source", {"kind": "rss", "value": "https://example.com/feed"}),
            ("end_reflection", {"notes": "removed stale feed."}),
        ],
    )
    record = await reflect_now(store=store, triggering_event=event, runner=runner)

    assert store.list_sources() == []
    assert record is not None
    assert record["sources_changed"] == 1


@pytest.mark.asyncio
async def test_reflection_add_source_duplicate_is_error(tmp_data_dir: Path):
    """add_source returns isError when the source is already in sources.yaml."""
    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.sources_path.write_text(
        "rss:\n  - url: https://example.com/feed\n    tags: []\nyoutube: []\nsites: []\n",
        encoding="utf-8",
    )
    from digest.agent import RunState, build_reflection_tools

    state = RunState(store=store, today=date(2026, 4, 29))
    build_reflection_tools(state)
    handler = state.current_tools["add_source"]
    result = await handler({"kind": "rss", "value": "https://example.com/feed"})
    assert result.get("isError") is True


class _DrainScriptedRunner:
    """Yields the same end_reflection script for each agent.run() call,
    so reflect_drain can iterate over multiple events without test plumbing."""

    def __init__(self, get_state: Callable[[], RunState | None]) -> None:
        self._get_state = get_state

    async def run(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        state = self._get_state()
        assert state is not None
        script = [("end_reflection", {"notes": "no-op"})]
        async for msg in ScriptedAgentRunner(state, script).run(prompt, options):
            yield msg


@pytest.mark.asyncio
async def test_reflect_drain_processes_each_event_and_advances_cursor(
    tmp_data_dir: Path, captured_state: dict[str, RunState | None]
) -> None:
    from digest.runner import reflect_drain

    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.append_feedback({"item_id": "a", "kind": "thumb", "value": "up"})
    store.append_feedback({"item_id": "b", "kind": "thumb", "value": "down"})

    runner = _DrainScriptedRunner(get_state=lambda: captured_state["state"])
    processed = await reflect_drain(store=store, runner=runner)

    assert processed == 2
    runs = store.read_recent_runs(days=1)
    assert len(runs) == 2
    assert all(r["kind"] == "reflection" for r in runs)
    # Each reflection run carries the feedback event that triggered it.
    assert all(r.get("triggering_feedback") for r in runs)
    cursor = store.read_reflection_cursor()
    assert cursor is not None
    # All events should be at-or-before the cursor now.
    feedback = store.read_recent_feedback(days=1)
    assert max(e["ts"] for e in feedback) == cursor


@pytest.mark.asyncio
async def test_reflect_drain_skips_when_lock_held(
    tmp_data_dir: Path, captured_state: dict[str, RunState | None]
) -> None:
    from digest.runner import reflect_drain

    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.append_feedback({"item_id": "a", "kind": "thumb", "value": "up"})

    # Pre-acquire the lock to simulate another worker.
    held = store.try_acquire_reflection_lock(ttl_seconds=900)
    assert held is not None

    runner = _DrainScriptedRunner(get_state=lambda: captured_state["state"])
    processed = await reflect_drain(store=store, runner=runner)
    assert processed == 0
    assert store.read_recent_runs(days=1) == []


class _FailingRunner:
    """Mimics the prod failure mode: agent.run() raises immediately."""

    async def run(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        raise RuntimeError("Command failed with exit code 1")
        yield  # pragma: no cover -- unreachable, but makes this an async generator


@pytest.mark.asyncio
async def test_reflect_now_persists_run_record_when_agent_fails(
    tmp_data_dir: Path,
) -> None:
    """A failed reflection must still leave a run record so the user can see it."""
    from digest.runner import reflect_now

    store = Store(tmp_data_dir)
    store.ensure_layout()
    event = {
        "ts": "2026-05-10T12:00:00+00:00",
        "item_id": "x",
        "kind": "thumb",
        "value": "down",
    }

    record = await reflect_now(store=store, triggering_event=event, runner=_FailingRunner())

    assert record is not None
    assert record["kind"] == "reflection"
    assert record["exit_reason"] == "error"
    assert "exit code 1" in record["error"]
    assert record["triggering_feedback"] == event

    persisted = store.read_recent_runs(days=1)
    assert len(persisted) == 1
    assert persisted[0]["exit_reason"] == "error"
    assert "exit code 1" in persisted[0]["error"]


@pytest.mark.asyncio
async def test_reflect_drain_does_not_advance_cursor_on_persistence_failure(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the store can't persist, leave the cursor alone so events get retried."""
    from digest.runner import reflect_drain

    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.append_feedback({"item_id": "a", "kind": "thumb", "value": "up"})

    def _boom(_record: dict[str, Any]) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(store, "append_run", _boom)

    processed = await reflect_drain(store=store, runner=_FailingRunner())

    assert processed == 0
    assert store.read_reflection_cursor() is None
    assert store.read_recent_runs(days=1) == []


@pytest.mark.asyncio
async def test_reflect_drain_records_failed_reflection_and_advances_cursor(
    tmp_data_dir: Path,
) -> None:
    """End-to-end regression: agent fails → run shows up in the list with exit_reason=error.

    This is the prod failure mode the user reported (\"reflections aren't showing\").
    A failed agent run must still leave a visible record."""
    from digest.runner import reflect_drain

    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.append_feedback({"item_id": "a", "kind": "thumb", "value": "up"})

    processed = await reflect_drain(store=store, runner=_FailingRunner())

    assert processed == 1
    runs = store.read_recent_runs(days=1)
    assert len(runs) == 1
    assert runs[0]["kind"] == "reflection"
    assert runs[0]["exit_reason"] == "error"
    assert store.read_reflection_cursor() is not None


@pytest.mark.asyncio
async def test_reflection_read_recent_feedback_filters_to_pre_trigger(
    tmp_data_dir: Path,
) -> None:
    """Reflection sees only events strictly before the trigger, plus the trigger
    itself appended last. This prevents agent #1 from acting on feedback meant
    for agent #2."""
    from digest.agent import RunState, build_reflection_tools

    store = Store(tmp_data_dir)
    store.ensure_layout()
    # Three events: one before, one trigger, one after.
    store.append_feedback({"ts": "2026-05-10T10:00:00+00:00", "kind": "chat", "text": "earlier"})
    store.append_feedback({"ts": "2026-05-10T11:00:00+00:00", "kind": "chat", "text": "trigger"})
    store.append_feedback({"ts": "2026-05-10T12:00:00+00:00", "kind": "chat", "text": "later"})

    trigger = {"ts": "2026-05-10T11:00:00+00:00", "kind": "chat", "text": "trigger"}
    state = RunState(store=store, today=date(2026, 5, 10))
    state.triggering_event = trigger
    build_reflection_tools(state)

    result = await state.current_tools["read_recent_feedback"]({"days": 14})
    text = result["content"][0]["text"]
    assert "earlier" in text
    assert "trigger" in text
    assert "later" not in text
    assert "--- triggering event ---" in text


@pytest.mark.asyncio
async def test_read_triggering_curation_run_returns_run_and_snapshot(
    tmp_data_dir: Path,
) -> None:
    """For thumb feedback, the tool resolves item_id → run and exposes the
    profile snapshot captured at curation time."""
    from digest.agent import RunState, build_reflection_tools

    store = Store(tmp_data_dir)
    store.ensure_layout()
    # Seed a curation run that surfaced item 'abc12345' with a captured snapshot.
    store.append_run(
        {
            "run_id": "cur1",
            "kind": "curation",
            "started_at": "2026-05-10T08:00:00+00:00",
            "submitted_item_ids": ["abc12345", "other001"],
            "tool_log": [
                {
                    "ts": "2026-05-10T08:00:01+00:00",
                    "tool": "read_profile",
                    "args": {},
                    "outcome": "123 chars",
                    "profile_snapshot": "# Profile\n- LLMs and woodworking\n",
                },
                {
                    "ts": "2026-05-10T08:00:30+00:00",
                    "tool": "submit_digest",
                    "args": {"count": 2},
                    "outcome": "ok",
                },
            ],
        }
    )

    trigger = {
        "ts": "2026-05-10T11:00:00+00:00",
        "kind": "thumb",
        "value": "down",
        "item_id": "abc12345",
    }
    state = RunState(store=store, today=date(2026, 5, 10))
    state.triggering_event = trigger
    build_reflection_tools(state)

    result = await state.current_tools["read_triggering_curation_run"]({})
    text = result["content"][0]["text"]
    assert "cur1" in text
    assert "LLMs and woodworking" in text
    assert "Profile snapshot at curation time" in text


@pytest.mark.asyncio
async def test_read_triggering_curation_run_uses_top_level_snapshot(
    tmp_data_dir: Path,
) -> None:
    """New curation runs store profile_snapshot at the top level of the run
    record (since the read_profile tool was removed from curation). The
    reflection lookup should prefer that over scanning tool_log."""
    from digest.agent import RunState, build_reflection_tools

    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.append_run(
        {
            "run_id": "cur2",
            "kind": "curation",
            "started_at": "2026-05-10T08:00:00+00:00",
            "submitted_item_ids": ["xyz98765"],
            "profile_snapshot": "# Profile\n- top-level snapshot path\n",
            "tool_log": [],
        }
    )
    state = RunState(store=store, today=date(2026, 5, 10))
    state.triggering_event = {
        "ts": "2026-05-10T11:00:00+00:00",
        "kind": "thumb",
        "value": "down",
        "item_id": "xyz98765",
    }
    build_reflection_tools(state)
    result = await state.current_tools["read_triggering_curation_run"]({})
    text = result["content"][0]["text"]
    assert "top-level snapshot path" in text


@pytest.mark.asyncio
async def test_read_triggering_curation_run_legacy_run_without_snapshot(
    tmp_data_dir: Path,
) -> None:
    """Old curation runs lacking profile_snapshot return a graceful message."""
    from digest.agent import RunState, build_reflection_tools

    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.append_run(
        {
            "run_id": "legacy",
            "kind": "curation",
            "started_at": "2026-05-10T08:00:00+00:00",
            "submitted_item_ids": ["abc12345"],
            "tool_log": [
                {
                    "ts": "2026-05-10T08:00:01+00:00",
                    "tool": "read_profile",
                    "args": {},
                    "outcome": "100 chars",
                },
            ],
        }
    )
    trigger = {
        "ts": "2026-05-10T11:00:00+00:00",
        "kind": "thumb",
        "value": "down",
        "item_id": "abc12345",
    }
    state = RunState(store=store, today=date(2026, 5, 10))
    state.triggering_event = trigger
    build_reflection_tools(state)

    result = await state.current_tools["read_triggering_curation_run"]({})
    text = result["content"][0]["text"]
    assert "snapshot unavailable" in text
    assert "legacy" in text


@pytest.mark.asyncio
async def test_read_triggering_curation_run_no_item_id(
    tmp_data_dir: Path,
) -> None:
    """Chat feedback (no item_id) returns a hint to use the fallback tool."""
    from digest.agent import RunState, build_reflection_tools

    store = Store(tmp_data_dir)
    store.ensure_layout()
    state = RunState(store=store, today=date(2026, 5, 10))
    state.triggering_event = {"ts": "2026-05-10T11:00:00+00:00", "kind": "chat", "text": "hello"}
    build_reflection_tools(state)

    result = await state.current_tools["read_triggering_curation_run"]({})
    text = result["content"][0]["text"]
    assert "no item_id" in text


@pytest.mark.asyncio
async def test_read_triggering_curation_run_item_not_found(
    tmp_data_dir: Path,
) -> None:
    from digest.agent import RunState, build_reflection_tools

    store = Store(tmp_data_dir)
    store.ensure_layout()
    state = RunState(store=store, today=date(2026, 5, 10))
    state.triggering_event = {
        "ts": "2026-05-10T11:00:00+00:00",
        "kind": "thumb",
        "value": "down",
        "item_id": "nonexistent",
    }
    build_reflection_tools(state)

    result = await state.current_tools["read_triggering_curation_run"]({})
    text = result["content"][0]["text"]
    assert "no curation run" in text


@pytest.mark.asyncio
async def test_reflection_memory_tools_round_trip(tmp_data_dir: Path) -> None:
    from digest.agent import RunState, build_reflection_tools

    store = Store(tmp_data_dir)
    store.ensure_layout()
    state = RunState(store=store, today=date(2026, 5, 10))
    build_reflection_tools(state)

    read1 = await state.current_tools["read_reflection_memory"]({})
    assert "Reflection memory" in read1["content"][0]["text"]

    write = await state.current_tools["write_reflection_memory"](
        {"text": "# Notes\n- watching political hot-takes\n"}
    )
    assert not write.get("isError")

    read2 = await state.current_tools["read_reflection_memory"]({})
    assert "political hot-takes" in read2["content"][0]["text"]


@pytest.mark.asyncio
async def test_write_reflection_memory_rejects_oversize(tmp_data_dir: Path) -> None:
    from digest.agent import RunState, build_reflection_tools

    store = Store(tmp_data_dir)
    store.ensure_layout()
    state = RunState(store=store, today=date(2026, 5, 10))
    build_reflection_tools(state)

    result = await state.current_tools["write_reflection_memory"]({"text": "x" * 5001})
    assert result.get("isError") is True
    # Memory was not overwritten.
    assert "xxxx" not in store.read_reflection_memory()


@pytest.mark.asyncio
async def test_curation_run_record_includes_profile_snapshot(
    tmp_data_dir: Path, captured_state: dict[str, RunState | None]
) -> None:
    """The persisted curation run record carries the profile snapshot that was
    baked into the system prompt, so a later reflection can quote it back via
    read_triggering_curation_run."""
    store = Store(tmp_data_dir)
    store.ensure_layout()
    store.write_profile("# Profile\n- woodworking, LLMs\n")

    runner = TwoPhaseScriptedRunner(
        get_state=lambda: captured_state["state"],
        curation=[("list_sources", {})],
        reflection=[("end_reflection", {"notes": "ok"})],
    )
    await run_once(data_dir=tmp_data_dir, runner=runner, today=date(2026, 5, 10))
    runs = [r for r in store.read_recent_runs(days=1) if r["kind"] == "curation"]
    assert len(runs) == 1
    snapshot = runs[0].get("profile_snapshot")
    assert isinstance(snapshot, str) and snapshot.startswith("# Profile")


@pytest.mark.asyncio
async def test_curation_run_record_includes_submitted_item_ids(
    tmp_data_dir: Path, captured_state: dict[str, RunState | None]
) -> None:
    """The persisted curation run record carries the list of submitted item ids
    so reflection can resolve thumb feedback to the originating run."""
    store = Store(tmp_data_dir)
    store.ensure_layout()
    curation = [
        (
            "submit_digest",
            {
                "items": [
                    {
                        "type": "article",
                        "title": "Why agents need evals",
                        "source": "Example",
                        "url": "https://example.com/post-1",
                        "summary": "x",
                    }
                ],
                "agent_notes": "n",
            },
        )
    ]
    runner = TwoPhaseScriptedRunner(
        get_state=lambda: captured_state["state"],
        curation=curation,
        reflection=[("end_reflection", {"notes": "ok"})],
    )
    await run_once(data_dir=tmp_data_dir, runner=runner, today=date(2026, 5, 10))
    runs = [r for r in store.read_recent_runs(days=1) if r["kind"] == "curation"]
    assert len(runs) == 1
    ids = runs[0].get("submitted_item_ids")
    assert isinstance(ids, list) and len(ids) == 1 and ids[0]


def test_runner_main_resolves_ssm_envvar_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reproduces the prod regression where reflection-via-Fargate launched
    `python -m digest.runner --reflect-drain` without resolving SSM env-var
    paths, so the bundled Claude CLI received the literal string
    `/morning-digest/claude-oauth-token` as its OAuth token and exited 1.

    `digest.runner.main()` must resolve SSM paths before any agent code path
    reads env vars."""
    import sys

    from digest import runner as runner_mod

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "/morning-digest/claude-oauth-token")
    monkeypatch.setattr("server.config._read_ssm", lambda name: "resolved-token-xyz")

    # Stub out the actual drain and asyncio.run so the test doesn't spin up
    # an event loop or talk to an agent — we only care that the env var is
    # resolved before main() reaches the agent code path.
    called = {"drain": False}

    async def _fake_drain(*, store: Any) -> int:
        called["drain"] = True
        return 0

    def _fake_asyncio_run(coro: Any) -> Any:
        coro.close()  # avoid "coroutine was never awaited" warning
        called["drain"] = True
        return 0

    monkeypatch.setattr(runner_mod, "reflect_drain", _fake_drain)
    monkeypatch.setattr(runner_mod.asyncio, "run", _fake_asyncio_run)
    monkeypatch.setattr(
        sys, "argv", ["digest.runner", "--reflect-drain", "--data-dir", str(tmp_path)]
    )

    runner_mod.main()

    import os

    assert called["drain"] is True
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "resolved-token-xyz"
