from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

import pytest

from digest import runner_fargate
from digest.runner import RunSummary


def _ok_summary() -> RunSummary:
    return RunSummary(
        run_id="abc12345",
        date=date(2026, 5, 14),
        item_count=3,
        started_at=datetime.now(UTC),
        duration_seconds=1.0,
        exit_reason="ok",
        profile_patches=0,
    )


@pytest.mark.asyncio
async def test_run_calls_reflect_drain_after_run_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_run_once(*, bucket: str) -> RunSummary:
        calls.append(f"run_once:{bucket}")
        return _ok_summary()

    async def fake_drain(*, store: Any) -> int:
        calls.append("drain")
        return 0

    monkeypatch.setattr(runner_fargate, "run_once", fake_run_once)
    monkeypatch.setattr(runner_fargate, "reflect_drain", fake_drain)
    monkeypatch.setattr(runner_fargate, "S3Store", lambda bucket: object())

    exit_code = await runner_fargate._run("my-bucket")

    assert exit_code == 0
    assert calls == ["run_once:my-bucket", "drain"]


@pytest.mark.asyncio
async def test_run_swallows_drain_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_once(*, bucket: str) -> RunSummary:
        return _ok_summary()

    async def failing_drain(*, store: Any) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_fargate, "run_once", fake_run_once)
    monkeypatch.setattr(runner_fargate, "reflect_drain", failing_drain)
    monkeypatch.setattr(runner_fargate, "S3Store", lambda bucket: object())

    # A failing drain must not bring down the curation exit status — curation
    # already succeeded, and pending events will be retried on the next run.
    exit_code = await runner_fargate._run("my-bucket")
    assert exit_code == 0


def test_main_exits_with_signum_on_signal_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import signal

    monkeypatch.setenv("MORNING_DIGEST_S3_BUCKET", "b")
    monkeypatch.setattr(runner_fargate, "resolve_ssm_env_vars", lambda: None)
    monkeypatch.setattr(runner_fargate, "_configure_logging", lambda: None)
    monkeypatch.setattr(runner_fargate, "_received_signal", signal.SIGTERM.value)

    def fake_run(coro: Any) -> int:
        coro.close()
        raise __import__("asyncio").CancelledError()

    monkeypatch.setattr(runner_fargate.asyncio, "run", fake_run)

    with pytest.raises(SystemExit) as exc:
        runner_fargate.main()
    assert exc.value.code == 128 + signal.SIGTERM.value  # 143


def test_main_exits_with_one_on_non_signal_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MORNING_DIGEST_S3_BUCKET", "b")
    monkeypatch.setattr(runner_fargate, "resolve_ssm_env_vars", lambda: None)
    monkeypatch.setattr(runner_fargate, "_configure_logging", lambda: None)
    monkeypatch.setattr(runner_fargate, "_received_signal", None)

    def fake_run(coro: Any) -> int:
        coro.close()
        raise __import__("asyncio").CancelledError()

    monkeypatch.setattr(runner_fargate.asyncio, "run", fake_run)

    with pytest.raises(SystemExit) as exc:
        runner_fargate.main()
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_run_returns_error_when_curation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_once(*, bucket: str) -> RunSummary:
        s = _ok_summary()
        s.exit_reason = "error"
        return s

    async def fake_drain(*, store: Any) -> int:
        return 0

    monkeypatch.setattr(runner_fargate, "run_once", fake_run_once)
    monkeypatch.setattr(runner_fargate, "reflect_drain", fake_drain)
    monkeypatch.setattr(runner_fargate, "S3Store", lambda bucket: object())

    exit_code = await runner_fargate._run("my-bucket")
    assert exit_code == 1


@pytest.mark.asyncio
async def test_run_signal_handler_cancels_main_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SIGTERM arrives mid-run, `_on_signal` records the signum, logs,
    and cancels the main task. We invoke the registered handler directly by
    spying on `loop.add_signal_handler`."""
    import signal

    captured: dict[str, Any] = {"handler": None, "args": None}

    class _FakeLoop:
        def add_signal_handler(self, sig: int, fn: Any, *args: Any) -> None:
            # Capture the SIGTERM handler with its bound args.
            if sig == signal.SIGTERM:
                captured["handler"] = fn
                captured["args"] = args

    monkeypatch.setattr(runner_fargate.asyncio, "get_running_loop", lambda: _FakeLoop())

    async def fake_run_once(*, bucket: str) -> RunSummary:
        # Fire the captured signal handler before completing the run.
        if captured["handler"] is not None:
            captured["handler"](*captured["args"])  # _on_signal(SIGTERM, "SIGTERM")
        return _ok_summary()

    async def fake_drain(*, store: Any) -> int:
        # Yield once so the cancellation set by _on_signal during run_once
        # actually fires before we return.
        await asyncio.sleep(0)
        return 0

    monkeypatch.setattr(runner_fargate, "run_once", fake_run_once)
    monkeypatch.setattr(runner_fargate, "reflect_drain", fake_drain)
    monkeypatch.setattr(runner_fargate, "S3Store", lambda bucket: object())
    monkeypatch.setattr(runner_fargate, "_received_signal", None)

    # The main task gets cancelled inside fake_run_once via _on_signal; the
    # next await yields control and the cancellation propagates outward.
    with pytest.raises(asyncio.CancelledError):
        await runner_fargate._run("my-bucket")
    assert runner_fargate._received_signal == signal.SIGTERM.value


def test_main_exits_one_when_bucket_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If MORNING_DIGEST_S3_BUCKET is unset, main() prints to stderr and exits 1."""
    monkeypatch.delenv("MORNING_DIGEST_S3_BUCKET", raising=False)
    monkeypatch.setattr(runner_fargate, "_configure_logging", lambda: None)
    monkeypatch.setattr(runner_fargate, "resolve_ssm_env_vars", lambda: None)
    with pytest.raises(SystemExit) as exc:
        runner_fargate.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "MORNING_DIGEST_S3_BUCKET" in err


def test_main_exits_zero_on_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() should resolve SSM, run `_run`, and exit with the returned code."""
    monkeypatch.setenv("MORNING_DIGEST_S3_BUCKET", "b")
    monkeypatch.setattr(runner_fargate, "_configure_logging", lambda: None)
    monkeypatch.setattr(runner_fargate, "resolve_ssm_env_vars", lambda: None)

    def fake_run(coro: Any) -> int:
        coro.close()
        return 0

    monkeypatch.setattr(runner_fargate.asyncio, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        runner_fargate.main()
    assert exc.value.code == 0
