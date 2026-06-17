import { afterEach, describe, expect, it, vi } from "vitest";
import {
  APP_TIME_ZONE,
  appToday,
  appTodayLong,
  dayKeyOf,
  formatDayLong,
  formatInstantLong,
  formatInstantTime,
} from "../time";

// The instant 2026-06-17T00:30:00Z is 8:30 PM on June 16 in America/New_York
// (EDT, UTC-4). It is the canonical "UTC has already rolled to the next day,
// but the app's day has not" case — the exact situation that produced the
// original "dates are a day off" bug.
const EVENING_ET = "2026-06-17T00:30:00Z";
const MORNING_ET = "2026-06-16T11:00:00Z"; // 7:00 AM ET, same UTC + ET day

afterEach(() => {
  vi.useRealTimers();
});

describe("APP_TIME_ZONE", () => {
  it("is the timezone the backend and scheduler are anchored to", () => {
    expect(APP_TIME_ZONE).toBe("America/New_York");
  });
});

describe("formatDayLong", () => {
  it("renders a plain calendar date without shifting it a day", () => {
    // The whole point: 2026-06-16 must read as June 16, never June 15.
    const out = formatDayLong("2026-06-16");
    expect(out).toMatch(/Tuesday/);
    expect(out).toMatch(/June 16/);
    expect(out).not.toMatch(/15/);
  });

  it("includes the year when asked", () => {
    expect(formatDayLong("2026-06-16", { year: true })).toMatch(/2026/);
  });

  it("omits the year by default", () => {
    expect(formatDayLong("2026-06-16")).not.toMatch(/2026/);
  });
});

describe("dayKeyOf", () => {
  it("maps a UTC instant to the app-timezone calendar day", () => {
    // 00:30 UTC on the 17th is still the 16th in Eastern time.
    expect(dayKeyOf(EVENING_ET)).toBe("2026-06-16");
    expect(dayKeyOf(MORNING_ET)).toBe("2026-06-16");
  });
});

describe("appToday / appTodayLong", () => {
  it("reports the app-timezone day even when UTC has rolled over", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(EVENING_ET));
    expect(appToday()).toBe("2026-06-16");
    expect(appTodayLong()).toMatch(/Tuesday/);
    expect(appTodayLong()).toMatch(/June 16/);
  });
});

describe("formatInstantTime", () => {
  it("formats the clock hour in the app timezone, not UTC", () => {
    // 00:30 UTC → 8 PM Eastern.
    expect(formatInstantTime(EVENING_ET)).toBe("8 PM");
  });

  it("accepts custom format options", () => {
    expect(formatInstantTime(EVENING_ET, { hour: "numeric", minute: "2-digit" })).toBe("8:30 PM");
  });
});

describe("formatInstantLong", () => {
  it("renders a full date+time label in the app timezone", () => {
    const out = formatInstantLong(EVENING_ET);
    expect(out).toMatch(/June 16, 2026/);
    expect(out).toMatch(/8:30/);
  });
});
