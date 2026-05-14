"""Shared fixtures: tmp data dir, fake-LLM scripted runner, mock httpx clients."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from digest.store import Store


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Iterator[Path]:
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
