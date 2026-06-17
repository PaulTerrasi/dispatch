/**
 * Single source of truth for date/time display.
 *
 * The backend assigns every digest / run / feedback a calendar day in the
 * application's timezone (America/New_York — see digest/clock.py and the
 * EventBridge scheduler in infra/app.py). The frontend therefore:
 *
 *   1. renders backend "YYYY-MM-DD" date keys as *plain calendar dates*, with
 *      no timezone conversion, so a date can never shift across midnight, and
 *   2. formats UTC instants (run times, "now") explicitly in the app timezone,
 *      rather than relying on the viewer's browser being set to it.
 *
 * Older code did ad-hoc `new Date(iso)` math that only happened to be correct
 * when the browser's timezone matched the server's — the source of the
 * "dates are a day off" bug. Route all date/time rendering through here.
 *
 * APP_TIME_ZONE must match $MORNING_DIGEST_TZ / the infra scheduler timezone.
 */
export const APP_TIME_ZONE = "America/New_York";

// en-CA formats as ISO-style "YYYY-MM-DD", which is exactly the key shape the
// backend uses, so day keys round-trip without parsing.
const dayKeyFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: APP_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/**
 * Render a backend "YYYY-MM-DD" calendar date as a long label
 * (e.g. "Tuesday, June 16"), with NO timezone shift.
 *
 * The date is anchored at local noon before formatting so that
 * `toLocaleDateString` can never roll it onto an adjacent day across a
 * midnight or DST boundary — only the Y/M/D we were handed matters.
 */
export function formatDayLong(isoDate: string, opts: { year?: boolean } = {}): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const dt = new Date(y, m - 1, d, 12);
  return dt.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    ...(opts.year ? { year: "numeric" } : {}),
  });
}

/** The app-timezone calendar day ("YYYY-MM-DD") that a UTC instant belongs to. */
export function dayKeyOf(iso: string): string {
  return dayKeyFmt.format(new Date(iso));
}

/** Today's calendar day ("YYYY-MM-DD") in the app timezone. */
export function appToday(): string {
  return dayKeyFmt.format(new Date());
}

/** Long label for the app's current day (e.g. "Tuesday, June 16"). */
export function appTodayLong(): string {
  return formatDayLong(appToday());
}

/**
 * Format a UTC instant's clock time in the app timezone (e.g. "8 PM").
 * Defaults to the hour only; pass options for finer control.
 */
export function formatInstantTime(
  iso: string,
  opts: Intl.DateTimeFormatOptions = { hour: "numeric" },
): string {
  return new Date(iso).toLocaleTimeString("en-US", { timeZone: APP_TIME_ZONE, ...opts });
}

/** Format a UTC instant as a full date + time label in the app timezone. */
export function formatInstantLong(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    timeZone: APP_TIME_ZONE,
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
