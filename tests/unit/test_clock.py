"""Tests for digest.clock — the app-timezone day source of truth.

The behavior that matters: an instant late in the evening (local) must bucket to
*that* local day, even though it has already crossed midnight in UTC. This is the
exact failure that made digest dates show up a day off.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from digest import clock

# 00:30 UTC on the 17th == 8:30 PM on the 16th in America/New_York (EDT).
EVENING_ET = "2026-06-17T00:30:00+00:00"
# 11:00 UTC == 7:00 AM ET on the 16th — same calendar day in both zones.
MORNING_ET = "2026-06-16T11:00:00+00:00"


def test_day_of_evening_instant_stays_on_local_day(monkeypatch):
    monkeypatch.setenv("MORNING_DIGEST_TZ", "America/New_York")
    assert clock.day_of(EVENING_ET) == date(2026, 6, 16)
    assert clock.day_of(MORNING_ET) == date(2026, 6, 16)


def test_day_of_accepts_datetime_and_treats_naive_as_utc(monkeypatch):
    monkeypatch.setenv("MORNING_DIGEST_TZ", "America/New_York")
    aware = datetime(2026, 6, 17, 0, 30, tzinfo=ZoneInfo("UTC"))
    naive = datetime(2026, 6, 17, 0, 30)  # no tzinfo -> interpreted as UTC
    assert clock.day_of(aware) == date(2026, 6, 16)
    assert clock.day_of(naive) == date(2026, 6, 16)


def test_timezone_is_configurable(monkeypatch):
    monkeypatch.setenv("MORNING_DIGEST_TZ", "UTC")
    assert clock.day_of(EVENING_ET) == date(2026, 6, 17)
    monkeypatch.setenv("MORNING_DIGEST_TZ", "America/New_York")
    assert clock.day_of(EVENING_ET) == date(2026, 6, 16)


def test_default_timezone_when_unset(monkeypatch):
    monkeypatch.delenv("MORNING_DIGEST_TZ", raising=False)
    assert str(clock.app_tz()) == clock.DEFAULT_TZ == "America/New_York"


def test_today_and_now_are_consistent(monkeypatch):
    monkeypatch.setenv("MORNING_DIGEST_TZ", "America/New_York")
    n = clock.now()
    assert n.tzinfo is not None
    assert clock.day_of(n) == n.date()
    assert isinstance(clock.today(), date)
