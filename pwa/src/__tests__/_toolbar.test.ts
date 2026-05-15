import { describe, expect, it, beforeEach } from "vitest";
import {
  formatTodayLong,
  renderChrome,
  renderPageHeader,
  renderTabBar,
  renderToolbar,
} from "../views/_toolbar";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("renderPageHeader", () => {
  it("renders wordmark + title + horizon rule by default", () => {
    const el = renderPageHeader({ title: "Today" });
    document.body.appendChild(el);
    expect(document.querySelector(".page-wordmark")?.textContent).toBe("dispatch");
    expect(document.querySelector(".page-title")?.textContent).toBe("Today");
    expect(document.querySelector(".horizon-rule")).not.toBeNull();
  });

  it("renders subtitle when provided and skips horizon when opted out", () => {
    const el = renderPageHeader({
      title: "Archive",
      subtitle: "Every past digest",
      showHorizon: false,
    });
    document.body.appendChild(el);
    expect(document.querySelector(".page-subtitle")?.textContent).toBe("Every past digest");
    expect(document.querySelector(".horizon-rule")).toBeNull();
  });

  it("omits subtitle node when not provided", () => {
    const el = renderPageHeader({ title: "x" });
    document.body.appendChild(el);
    expect(document.querySelector(".page-subtitle")).toBeNull();
  });
});

describe("renderTabBar", () => {
  it("marks the active tab", () => {
    const nav = renderTabBar("chat");
    document.body.appendChild(nav);
    const active = document.querySelector(".tabbar-item.active") as HTMLAnchorElement;
    expect(active.getAttribute("aria-label")).toBe("Chat");
    expect(active.getAttribute("aria-current")).toBe("page");
  });

  it("renders one tab per primary key", () => {
    document.body.appendChild(renderTabBar("today"));
    expect(document.querySelectorAll(".tabbar-item").length).toBe(4);
  });
});

describe("renderChrome", () => {
  it("mounts both the header and the tabbar onto a root", () => {
    const root = document.createElement("div");
    renderChrome(root, "runs", { title: "Runs" });
    expect(root.querySelector(".page-header")).not.toBeNull();
    expect(root.querySelector(".tabbar")).not.toBeNull();
    expect(root.querySelector(".tabbar-item.active")?.getAttribute("aria-label")).toBe("Runs");
  });
});

describe("renderToolbar (legacy)", () => {
  it("renders the wordmark and nav for older views", () => {
    document.body.appendChild(renderToolbar("today"));
    expect(document.querySelector(".toolbar strong")?.textContent).toBe("Dispatch");
    expect(document.querySelectorAll(".tabbar-item").length).toBe(4);
  });
});

describe("formatTodayLong", () => {
  it("returns a long date string (weekday, month, day)", () => {
    const out = formatTodayLong(new Date(2026, 4, 15));
    // The exact format depends on the runtime locale, but it must contain the
    // year-independent month name and day number.
    expect(out).toMatch(/May/);
    expect(out).toMatch(/15/);
  });
});
