import { describe, expect, it, vi, beforeEach } from "vitest";
import { forceRefresh, navigate, startRouter } from "../router";

beforeEach(() => {
  document.body.innerHTML = "";
  location.hash = "";
});

describe("startRouter", () => {
  it("matches a static path and renders into the mount element", async () => {
    const mount = document.createElement("div");
    const today = vi.fn(async () => {
      const el = document.createElement("p");
      el.textContent = "today";
      return el;
    });
    location.hash = "#/today";
    startRouter([{ path: "/today", render: today }], mount);
    // wait a tick for async render
    await new Promise((r) => setTimeout(r, 0));
    expect(today).toHaveBeenCalled();
    expect(mount.textContent).toBe("today");
  });

  it("extracts named params", async () => {
    const mount = document.createElement("div");
    const captured: Record<string, string>[] = [];
    location.hash = "#/digest/2026-04-29";
    startRouter(
      [
        {
          path: "/digest/:date",
          render: (params) => {
            captured.push(params);
            const el = document.createElement("span");
            el.textContent = `d=${params.date}`;
            return el;
          },
        },
      ],
      mount,
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(captured).toEqual([{ date: "2026-04-29" }]);
    expect(mount.textContent).toBe("d=2026-04-29");
  });

  it("defaults to /today when the hash is empty", async () => {
    const mount = document.createElement("div");
    const home = vi.fn(async () => {
      const el = document.createElement("p");
      el.textContent = "home";
      return el;
    });
    location.hash = "";
    startRouter([{ path: "/today", render: home }], mount);
    await new Promise((r) => setTimeout(r, 0));
    expect(home).toHaveBeenCalled();
  });

  it("renders a Page not found element when no route matches", async () => {
    const mount = document.createElement("div");
    location.hash = "#/no/such/path";
    startRouter([{ path: "/today", render: () => document.createElement("p") }], mount);
    await new Promise((r) => setTimeout(r, 0));
    expect(mount.textContent).toContain("Page not found");
  });

  it("shows an error message when a render() throws", async () => {
    const mount = document.createElement("div");
    location.hash = "#/boom";
    startRouter(
      [
        {
          path: "/boom",
          render: () => {
            throw new Error("render failed");
          },
        },
      ],
      mount,
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(mount.textContent).toContain("Failed to load");
  });

  it("ignores re-entrant renders for the same path", async () => {
    const mount = document.createElement("div");
    const home = vi.fn(async () => document.createElement("p"));
    location.hash = "#/today";
    startRouter([{ path: "/today", render: home }], mount);
    await new Promise((r) => setTimeout(r, 0));
    // Dispatching another hashchange to the same path should NOT re-render.
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    await new Promise((r) => setTimeout(r, 0));
    expect(home).toHaveBeenCalledTimes(1);
  });

  it("forceRefresh re-renders the current path", async () => {
    const mount = document.createElement("div");
    const home = vi.fn(async () => document.createElement("p"));
    location.hash = "#/today";
    startRouter([{ path: "/today", render: home }], mount);
    await new Promise((r) => setTimeout(r, 0));
    forceRefresh();
    await new Promise((r) => setTimeout(r, 0));
    expect(home).toHaveBeenCalledTimes(2);
  });

  it("defers handle() until DOMContentLoaded when readyState is 'loading'", async () => {
    Object.defineProperty(document, "readyState", {
      configurable: true,
      get: () => "loading",
    });
    try {
      const mount = document.createElement("div");
      const home = vi.fn(async () => document.createElement("p"));
      location.hash = "#/today";
      startRouter([{ path: "/today", render: home }], mount);
      // No DOMContentLoaded fired yet, so render is deferred.
      expect(home).not.toHaveBeenCalled();
      window.dispatchEvent(new Event("DOMContentLoaded"));
      await new Promise((r) => setTimeout(r, 0));
      expect(home).toHaveBeenCalled();
    } finally {
      Object.defineProperty(document, "readyState", {
        configurable: true,
        get: () => "complete",
      });
    }
  });

  it("navigate(path) updates location.hash and triggers a render", async () => {
    const mount = document.createElement("div");
    const renderA = vi.fn(async () => document.createElement("p"));
    const renderB = vi.fn(async () => document.createElement("p"));
    location.hash = "#/a";
    startRouter(
      [
        { path: "/a", render: renderA },
        { path: "/b", render: renderB },
      ],
      mount,
    );
    await new Promise((r) => setTimeout(r, 0));
    navigate("/b");
    await new Promise((r) => setTimeout(r, 0));
    expect(renderB).toHaveBeenCalled();
  });
});
