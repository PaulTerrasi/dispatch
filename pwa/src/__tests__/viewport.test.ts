import { afterEach, describe, expect, it, vi } from "vitest";

import { initViewportTracker } from "../viewport";

describe("initViewportTracker", () => {
  const originalVV = window.visualViewport;

  afterEach(() => {
    Object.defineProperty(window, "visualViewport", {
      configurable: true,
      value: originalVV,
    });
    document.documentElement.style.removeProperty("--viewport-h");
  });

  it("mirrors visualViewport.height into --viewport-h and listens for resize/scroll", () => {
    const listeners: Record<string, () => void> = {};
    const fakeVV = {
      height: 600,
      addEventListener: vi.fn((type: string, fn: () => void) => {
        listeners[type] = fn;
      }),
    };
    Object.defineProperty(window, "visualViewport", {
      configurable: true,
      value: fakeVV,
    });

    initViewportTracker();
    expect(document.documentElement.style.getPropertyValue("--viewport-h")).toBe("600px");
    expect(fakeVV.addEventListener).toHaveBeenCalledWith("resize", expect.any(Function));
    expect(fakeVV.addEventListener).toHaveBeenCalledWith("scroll", expect.any(Function));

    fakeVV.height = 320;
    listeners.resize();
    expect(document.documentElement.style.getPropertyValue("--viewport-h")).toBe("320px");
  });

  it("falls back to window.innerHeight + window resize when visualViewport is absent", () => {
    Object.defineProperty(window, "visualViewport", {
      configurable: true,
      value: undefined,
    });
    const addSpy = vi.spyOn(window, "addEventListener");
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 480 });

    initViewportTracker();
    expect(document.documentElement.style.getPropertyValue("--viewport-h")).toBe("480px");
    expect(addSpy).toHaveBeenCalledWith("resize", expect.any(Function));

    addSpy.mockRestore();
  });
});
