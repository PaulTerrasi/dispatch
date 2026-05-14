from __future__ import annotations

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
