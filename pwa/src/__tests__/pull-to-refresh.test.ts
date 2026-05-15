import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

beforeEach(() => {
  document.body.innerHTML = "";
  // jsdom doesn't implement window.scrollTo; the production code only uses
  // window.scrollY directly, so just reset that.
  Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function fireTouch(type: "touchstart" | "touchmove" | "touchend", y: number) {
  // jsdom doesn't implement TouchEvent/Touch; fake the minimum shape we need.
  const touchList = [{ clientY: y, clientX: 0 }];
  const ev = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(ev, {
    touches: { value: touchList },
    changedTouches: { value: touchList },
  });
  document.dispatchEvent(ev);
  return ev;
}

describe("initPullToRefresh", () => {
  it("creates an indicator element on the body", async () => {
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(null);
    expect(document.querySelector(".ptr-indicator")).not.toBeNull();
  });

  it("pulling past the threshold triggers forceRefresh when no SW registration", async () => {
    vi.useFakeTimers();
    const router = await import("../router");
    const spy = vi.spyOn(router, "forceRefresh");
    spy.mockClear();
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(null);

    fireTouch("touchstart", 0);
    fireTouch("touchmove", 100);
    fireTouch("touchend", 200); // delta = 200 > THRESHOLD (80)

    // After the 600ms reset delay the indicator collapses.
    await vi.advanceTimersByTimeAsync(700);
    expect(spy).toHaveBeenCalled();
    const indicator = document.querySelector(".ptr-indicator") as HTMLElement;
    expect(indicator.style.opacity).toBe("0");
  });

  it("touchmove with a downward delta updates the indicator transform/opacity", async () => {
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(null);
    fireTouch("touchstart", 10);
    fireTouch("touchmove", 50); // small positive delta
    const ind = document.querySelector(".ptr-indicator") as HTMLElement;
    expect(ind.style.opacity).not.toBe("");
    expect(ind.style.transform).toContain("translateY");
  });

  it("touchmove with a negative delta resets the indicator (user pulled up)", async () => {
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(null);
    fireTouch("touchstart", 100);
    fireTouch("touchmove", 50); // upward swipe
    const ind = document.querySelector(".ptr-indicator") as HTMLElement;
    expect(ind.style.opacity).toBe("0");
  });

  it("touchstart is ignored when the page is not at the top", async () => {
    Object.defineProperty(window, "scrollY", { value: 200, configurable: true });
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(null);
    fireTouch("touchstart", 0);
    fireTouch("touchmove", 100);
    const ind = document.querySelector(".ptr-indicator") as HTMLElement;
    // Indicator never moved because pulling=false.
    expect(ind.style.transform || "").toBe("");
    Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
  });

  it("touchend without crossing the threshold resets without refreshing", async () => {
    const router = await import("../router");
    const spy = vi.spyOn(router, "forceRefresh").mockImplementation(() => {});
    spy.mockClear();
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(null);
    fireTouch("touchstart", 0);
    fireTouch("touchmove", 30);
    fireTouch("touchend", 30); // delta < THRESHOLD
    expect(spy).not.toHaveBeenCalled();
  });

  it("touchend without pulling state is a no-op", async () => {
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(null);
    fireTouch("touchend", 200); // no prior touchstart
    // No crash; indicator stays default.
    expect(document.querySelector(".ptr-indicator")).not.toBeNull();
  });

  it("with a waiting SW, posts SKIP_WAITING (skip-waiting flow)", async () => {
    vi.useFakeTimers();
    const waiting = { postMessage: vi.fn() };
    const reg = {
      update: vi.fn(async () => {}),
      waiting,
    } as unknown as ServiceWorkerRegistration;
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(reg);

    fireTouch("touchstart", 0);
    fireTouch("touchmove", 100);
    fireTouch("touchend", 200);

    await vi.advanceTimersByTimeAsync(50);
    expect(waiting.postMessage).toHaveBeenCalledWith({ type: "SKIP_WAITING" });
  });

  it("with a registration but no waiting worker, still falls through to forceRefresh", async () => {
    vi.useFakeTimers();
    const router = await import("../router");
    const spy = vi.spyOn(router, "forceRefresh").mockImplementation(() => {});
    spy.mockClear();
    const reg = {
      update: vi.fn(async () => {}),
      waiting: null,
    } as unknown as ServiceWorkerRegistration;
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(reg);

    fireTouch("touchstart", 0);
    fireTouch("touchmove", 100);
    fireTouch("touchend", 200);
    await vi.advanceTimersByTimeAsync(700);
    expect(spy).toHaveBeenCalled();
  });

  it("swallows a failing registration.update without crashing", async () => {
    vi.useFakeTimers();
    const router = await import("../router");
    const spy = vi.spyOn(router, "forceRefresh").mockImplementation(() => {});
    spy.mockClear();
    const reg = {
      update: vi.fn(async () => {
        throw new Error("offline");
      }),
      waiting: null,
    } as unknown as ServiceWorkerRegistration;
    const { initPullToRefresh } = await import("../pull-to-refresh");
    initPullToRefresh(reg);

    fireTouch("touchstart", 0);
    fireTouch("touchmove", 100);
    fireTouch("touchend", 200);
    await vi.advanceTimersByTimeAsync(700);
    expect(spy).toHaveBeenCalled();
  });
});
