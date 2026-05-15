"""Shared fixtures: tmp data dir, fake-LLM scripted runner, mock httpx clients."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from digest.store import Store


@pytest.fixture
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `git commit` in tests independent of the user's global gitconfig.

    Some sandboxed environments force `commit.gpgsign = true` with a signing
    program that needs network access — that makes any subprocess `git commit`
    in tests crash. Pointing GIT_CONFIG_GLOBAL/SYSTEM at /dev/null gives every
    test an empty global config; the local-repo identity that
    Store.git_init_if_needed() writes is then enough.

    Pulled in transitively via `tmp_data_dir` so tests that build a Store get
    it for free; pure unit tests (RSS, patch, YouTube, etc.) skip the env
    patching entirely.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)


@pytest.fixture
def tmp_data_dir(tmp_path: Path, _isolate_git_env: None) -> Iterator[Path]:
    d = tmp_path / "data"
    d.mkdir()
    yield d


@pytest.fixture
def store(tmp_data_dir: Path) -> Store:
    s = Store(tmp_data_dir)
    s.ensure_layout()
    return s


@pytest.fixture
def today() -> date:
    return datetime.now(UTC).date()
