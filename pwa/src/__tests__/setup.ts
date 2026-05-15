/**
 * Vitest setup file. Loaded once before each test environment.
 *
 * jsdom doesn't implement everything the runtime uses; polyfill the bits
 * production code reaches for so tests don't crash on benign side-effects.
 */
import { vi } from "vitest";

// jsdom: no-op scrollIntoView so chat / today scroll calls don't throw.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function () {
    /* noop in tests */
  };
}

// jsdom doesn't implement matchMedia; trivial true-stub so any responsive
// code (or libraries that probe it) sees a stable result.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = vi.fn().mockReturnValue({
    matches: false,
    media: "",
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  });
}
