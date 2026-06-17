"""Single source of truth for the application's notion of a calendar day.

The product is anchored to one wall-clock timezone — the same zone the
EventBridge Scheduler fires in (see ``schedule_expression_timezone`` in
infra/app.py). Digests, runs, and feedback are bucketed by the *calendar day in
that timezone*, not by UTC: a curation run at 11pm Eastern belongs to that
Monday, even though it's already Tuesday in UTC.

Timestamps themselves are still stored as UTC instants (unambiguous, sortable);
only the day-bucketing and day-labeling go through here. Keeping this logic in
one module is what prevents the class of off-by-one bugs that come from
sprinkling ``datetime.now(UTC).date()`` throughout the codebase.

The timezone is read from ``$MORNING_DIGEST_TZ`` (default ``America/New_York``)
so it stays configurable and can be matched to the infra scheduler in one place.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/New_York"


def app_tz() -> ZoneInfo:
    """The application timezone. Override with ``$MORNING_DIGEST_TZ``.

    ``ZoneInfo`` caches instances internally, so calling this on every
    bucketing operation is cheap; reading the env var each call keeps it
    overridable in tests.
    """
    return ZoneInfo(os.environ.get("MORNING_DIGEST_TZ") or DEFAULT_TZ)


def now() -> datetime:
    """Current instant, expressed in the app timezone."""
    return datetime.now(app_tz())


def today() -> date:
    """Today's calendar date in the app timezone.

    This is the canonical replacement for ``datetime.now(UTC).date()`` anywhere
    a *day* (not an instant) is needed.
    """
    return now().date()


def day_of(ts: str | datetime) -> date:
    """The app-timezone calendar day a UTC instant belongs to.

    Accepts an ISO-8601 string (e.g. a stored ``started_at``) or a datetime.
    Naive values — those without tzinfo — are interpreted as UTC, matching how
    this app stores every timestamp.
    """
    dt = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(app_tz()).date()
