import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderDigest, renderToday } from "../views/today";
import type { Digest, FeedItem } from "../api";

const SEED: Digest = {
  date: "2026-04-29",
  generated_at: "2026-04-29T05:02:11Z",
  items: [
    {
      id: "abc12345",
      type: "article",
      title: "Why agents need evals",
      source: "Example",
      url: "https://example.com/post-1",
      summary: "A sober look at LLM agent evals.",
      feedback: null,
      run_id: "run00001",
    },
  ],
  agent_notes: "One keeper today.",
  runs: [
    {
      run_id: "run00001",
      kind: "curation",
      started_at: "2026-04-29T05:00:00+00:00",
      duration_seconds: 312,
      tool_calls: 14,
      profile_patches: 0,
      sources_changed: 0,
      reflection_notes: "",
      item_count: 1,
      triggering_feedback: null,
    },
    {
      run_id: "ref00001",
      kind: "reflection",
      started_at: "2026-04-29T05:08:00+00:00",
      duration_seconds: 18,
      tool_calls: 4,
      profile_patches: 1,
      sources_changed: 0,
      reflection_notes: "noted preference for sober eval coverage.",
      item_count: 0,
      triggering_feedback: { kind: "thumb", value: "up", item_id: "abc12345" },
    },
  ],
};

const FEED_SEED: FeedItem[] = [
  {
    id: "new-1",
    type: "article",
    title: "Newer article",
    source: "Example",
    url: "https://example.com/new-1",
    summary: "From today.",
    feedback: null,
    digest_date: "2026-04-29",
    run_id: "run00429",
    run_started_at: "2026-04-29T05:00:00+00:00",
  },
  {
    id: "old-1",
    type: "article",
    title: "Older article",
    source: "Example",
    url: "https://example.com/old-1",
    summary: "From last week.",
    feedback: null,
    digest_date: "2026-04-22",
    run_id: "run00422",
    run_started_at: "2026-04-22T05:00:00+00:00",
  },
];

function mockFetch(routes: Record<string, unknown>) {
  return vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
    async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      for (const [path, body] of Object.entries(routes)) {
        if (url.startsWith(path)) {
          return new Response(JSON.stringify(body), { status: 200 });
        }
      }
      return new Response('{"status":"ok"}', { status: 200 });
    },
  );
}

describe("renderDigest", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("renders item title and summary", () => {
    const el = renderDigest(structuredClone(SEED));
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("Why agents need evals");
    expect(document.body.textContent).toContain("sober look");
  });

  it("clicking thumb posts feedback and toggles state", async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      async () => new Response('{"status":"ok"}', { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    // Use a pre-reacted item so the click toggles state without triggering feed-removal.
    const seed = structuredClone(SEED);
    seed.items[0].feedback = "up";
    const el = renderDigest(seed);
    document.body.appendChild(el);
    const upBtn = document.querySelector(".thumb.up") as HTMLButtonElement;
    upBtn.click();
    await new Promise((r) => setTimeout(r, 0));

    expect(upBtn.classList.contains("active")).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/feedback",
      expect.objectContaining({ method: "POST" }),
    );
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init).toBeDefined();
    const body = JSON.parse(init!.body as string);
    // notes omitted when textarea is empty
    expect(body).toEqual({ item_id: "abc12345", value: "none" });
  });

  it("includes notes in POST body when textarea has text", async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      async () => new Response('{"status":"ok"}', { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const seed = structuredClone(SEED);
    seed.items[0].feedback = "up";
    const el = renderDigest(seed);
    document.body.appendChild(el);

    const textarea = document.querySelector(".feedback-notes") as HTMLTextAreaElement;
    textarea.value = "great piece";

    const upBtn = document.querySelector(".thumb.up") as HTMLButtonElement;
    upBtn.click();
    await new Promise((r) => setTimeout(r, 0));

    const init = fetchMock.mock.calls[0]?.[1];
    const body = JSON.parse(init!.body as string);
    expect(body.notes).toBe("great piece");
    // textarea should be cleared after submission
    expect(textarea.value).toBe("");
  });

  it("shows quiet-morning state when no items", () => {
    const empty: Digest = { ...SEED, items: [] };
    const el = renderDigest(empty);
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("Quiet morning");
  });
});

describe("renderToday (feed)", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    vi.useFakeTimers();
  });

  it("renders feed items grouped by digest date with date headers", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/profile/status": { has_profile: true },
        "/api/feed": FEED_SEED,
      }),
    );

    const el = await renderToday();
    document.body.appendChild(el);

    expect(document.body.textContent).toContain("Newer article");
    expect(document.body.textContent).toContain("Older article");
    const dateLines = document.querySelectorAll(".date-line");
    expect(dateLines.length).toBe(2);
  });

  it("removes the card from the feed when the user reacts for the first time", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/profile/status": { has_profile: true },
        "/api/feed": FEED_SEED,
      }),
    );

    const el = await renderToday();
    document.body.appendChild(el);

    expect(document.querySelectorAll("article.item").length).toBe(2);
    const upBtn = document.querySelector("article.item .thumb.up") as HTMLButtonElement;
    upBtn.click();
    await vi.runAllTimersAsync();

    expect(document.querySelectorAll("article.item").length).toBe(1);
  });

  it("shows inbox-zero empty state when feed is empty", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/profile/status": { has_profile: true },
        "/api/feed": [],
      }),
    );

    const el = await renderToday();
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("Inbox zero");
  });
});
