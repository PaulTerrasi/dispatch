import { describe, expect, it, vi } from "vitest";
import { startRouter } from "../router";

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
});
