/**
 * Smoke tests for the bootstrap module. main.ts wires the SW + router + p2r
 * together, so we mock every imported module and assert the wiring.
 *
 * NOTE: `"serviceWorker" in navigator` is true under jsdom, so we always
 * provide a serviceWorker mock and just vary `register`'s behavior.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = {
  startRouter: vi.fn(),
  forceRefresh: vi.fn(),
  initPullToRefresh: vi.fn(),
};

function applyDoMocks() {
  vi.doMock("../router", () => ({
    startRouter: mocks.startRouter,
    forceRefresh: mocks.forceRefresh,
    navigate: vi.fn(),
  }));
  vi.doMock("../pull-to-refresh", () => ({ initPullToRefresh: mocks.initPullToRefresh }));
  vi.doMock("../views/today", () => ({ renderToday: () => document.createElement("div") }));
  vi.doMock("../views/archive", () => ({ renderArchive: () => document.createElement("div") }));
  vi.doMock("../views/chat", () => ({ renderChat: () => document.createElement("div") }));
  vi.doMock("../views/digest_day", () => ({
    renderDigestDay: () => document.createElement("div"),
  }));
  vi.doMock("../views/run_detail", () => ({
    renderRunDetail: () => document.createElement("div"),
  }));
  vi.doMock("../views/runs", () => ({ renderRuns: () => document.createElement("div") }));
}

interface SwMock {
  register: (script: string) => Promise<{ waiting: ServiceWorker | null }>;
  addEventListener: (ev: string, fn: () => void) => void;
}

function installNavigatorSw(sw: SwMock) {
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    get: () => sw,
  });
}

beforeEach(() => {
  document.body.innerHTML = "";
  const app = document.createElement("div");
  app.id = "app";
  document.body.appendChild(app);
  vi.resetModules();
  mocks.startRouter.mockReset();
  mocks.initPullToRefresh.mockReset();
});

afterEach(() => {
  vi.doUnmock("../router");
  vi.doUnmock("../pull-to-refresh");
  vi.doUnmock("../views/today");
  vi.doUnmock("../views/archive");
  vi.doUnmock("../views/chat");
  vi.doUnmock("../views/digest_day");
  vi.doUnmock("../views/run_detail");
  vi.doUnmock("../views/runs");
});

describe("main.ts bootstrap", () => {
  it("calls startRouter with the six configured routes", async () => {
    applyDoMocks();
    installNavigatorSw({
      register: async () => ({ waiting: null }),
      addEventListener: () => {},
    });

    await import("../main");
    window.dispatchEvent(new Event("load"));
    await new Promise((r) => setTimeout(r, 0));

    expect(mocks.startRouter).toHaveBeenCalled();
    const routes = mocks.startRouter.mock.calls[0][0] as { path: string }[];
    const paths = routes.map((r) => r.path).sort();
    expect(paths).toEqual([
      "/archive",
      "/chat",
      "/digest/:date",
      "/run/:run_id",
      "/runs",
      "/today",
    ]);
  });

  it("throws when #app is missing", async () => {
    document.body.innerHTML = ""; // remove #app
    applyDoMocks();
    installNavigatorSw({
      register: async () => ({ waiting: null }),
      addEventListener: () => {},
    });
    await expect(import("../main")).rejects.toThrow("missing #app element");
  });

  it("registers the service worker and initPullToRefresh on success", async () => {
    applyDoMocks();
    const register = vi.fn(async () => ({ waiting: null }));
    const addEventListener = vi.fn();
    installNavigatorSw({ register, addEventListener });

    await import("../main");
    window.dispatchEvent(new Event("load"));
    await new Promise((r) => setTimeout(r, 5));

    expect(register).toHaveBeenCalledWith("/sw.js");
    expect(mocks.initPullToRefresh).toHaveBeenCalled();
    expect(addEventListener).toHaveBeenCalledWith("controllerchange", expect.any(Function));
  });

  it("falls through to initPullToRefresh(null) when SW registration fails", async () => {
    applyDoMocks();
    const register = vi.fn(async () => {
      throw new Error("SW boom");
    });
    installNavigatorSw({ register, addEventListener: () => {} });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    await import("../main");
    window.dispatchEvent(new Event("load"));
    await new Promise((r) => setTimeout(r, 5));

    expect(mocks.initPullToRefresh).toHaveBeenCalledWith(null);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("controllerchange handler reloads to apply the new app shell", async () => {
    applyDoMocks();
    let savedHandler: (() => void) | null = null;
    installNavigatorSw({
      register: async () => ({ waiting: null }),
      addEventListener: (ev, fn) => {
        if (ev === "controllerchange") savedHandler = fn;
      },
    });
    const reload = vi.fn();
    // jsdom's `location.reload` is a method on a non-configurable getter, so
    // we shadow the whole `location` accessor on this window.
    const origLoc = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      get: () => ({ ...origLoc, reload }),
    });
    try {
      await import("../main");
      window.dispatchEvent(new Event("load"));
      await new Promise((r) => setTimeout(r, 5));
      expect(savedHandler).not.toBeNull();
      savedHandler!();
      expect(reload).toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        get: () => origLoc,
      });
    }
  });
});
