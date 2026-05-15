import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderArchive } from "../views/archive";

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(routes: Record<string, unknown>) {
  return vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
    async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      for (const [path, body] of Object.entries(routes)) {
        if (url.startsWith(path)) {
          return new Response(JSON.stringify(body), { status: 200 });
        }
      }
      return new Response("[]", { status: 200 });
    },
  );
}

describe("renderArchive", () => {
  it("shows empty state when no summaries exist", async () => {
    vi.stubGlobal("fetch", stubFetch({ "/api/digests": [] }));
    const el = await renderArchive();
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("No digests yet");
  });

  it("renders one row per summary with correct counts and pluralization", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "/api/digests": [
          { date: "2026-05-14", item_count: 1, has_unread: false },
          { date: "2026-05-13", item_count: 3, has_unread: false },
        ],
      }),
    );
    const el = await renderArchive();
    document.body.appendChild(el);
    const rows = document.querySelectorAll(".archive-row");
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain("1 item");
    expect(rows[0].textContent).not.toContain("1 items");
    expect(rows[1].textContent).toContain("3 items");
  });

  it("Search button runs api.search and renders hits", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "/api/digests": [],
        "/api/search": [
          { date: "2026-05-14", item_id: "x", title: "Found", url: "u", snippet: "snippet…" },
        ],
      }),
    );
    const el = await renderArchive();
    document.body.appendChild(el);

    const input = document.querySelector('input[type="search"]') as HTMLInputElement;
    const btn = document.querySelector("button") as HTMLButtonElement;
    input.value = "found";
    btn.click();
    await new Promise((r) => setTimeout(r, 10));

    expect(document.body.textContent).toContain("Found");
    expect(document.body.textContent).toContain("snippet");
  });

  it("Search shows 'no matches' when api returns []", async () => {
    vi.stubGlobal("fetch", stubFetch({ "/api/digests": [], "/api/search": [] }));
    const el = await renderArchive();
    document.body.appendChild(el);

    const input = document.querySelector('input[type="search"]') as HTMLInputElement;
    input.value = "nothing";
    (document.querySelector("button") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 10));
    expect(document.body.textContent).toContain("No matches");
  });

  it("Empty query clears results and re-shows the date list", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "/api/digests": [{ date: "2026-05-14", item_count: 1, has_unread: false }],
        "/api/search": [],
      }),
    );
    const el = await renderArchive();
    document.body.appendChild(el);

    const input = document.querySelector('input[type="search"]') as HTMLInputElement;
    const btn = document.querySelector("button") as HTMLButtonElement;
    // Search with content first to hide the date list…
    input.value = "anything";
    btn.click();
    await new Promise((r) => setTimeout(r, 10));
    // …then clear and search again; date list comes back.
    input.value = "   ";
    btn.click();
    await new Promise((r) => setTimeout(r, 10));
    const dateList = document.querySelectorAll(".archive-row");
    expect(dateList.length).toBe(1);
  });

  it("Enter in the search box triggers a search", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "/api/digests": [],
        "/api/search": [{ date: "2026-05-14", item_id: "x", title: "Hit", url: "u", snippet: "" }],
      }),
    );
    const el = await renderArchive();
    document.body.appendChild(el);
    const input = document.querySelector('input[type="search"]') as HTMLInputElement;
    input.value = "hit";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await new Promise((r) => setTimeout(r, 10));
    expect(document.body.textContent).toContain("Hit");
  });

  it("Other keys in the search box do not trigger a fetch", async () => {
    const fetchMock = stubFetch({ "/api/digests": [], "/api/search": [] });
    vi.stubGlobal("fetch", fetchMock);
    const el = await renderArchive();
    document.body.appendChild(el);
    const input = document.querySelector('input[type="search"]') as HTMLInputElement;
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true }));
    await new Promise((r) => setTimeout(r, 10));
    // Only the initial /api/digests call.
    const calls = fetchMock.mock.calls.map((c) => c[0]);
    expect(calls.filter((u) => String(u).startsWith("/api/search")).length).toBe(0);
  });

  it("Hit without snippet doesn't render an empty <p>", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "/api/digests": [],
        "/api/search": [{ date: "2026-05-14", item_id: "x", title: "T", url: "u", snippet: "" }],
      }),
    );
    const el = await renderArchive();
    document.body.appendChild(el);
    const input = document.querySelector('input[type="search"]') as HTMLInputElement;
    input.value = "t";
    (document.querySelector("button") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 10));
    const hit = document.querySelector(".item") as HTMLElement;
    expect(hit.querySelector("p")).toBeNull();
  });
});
