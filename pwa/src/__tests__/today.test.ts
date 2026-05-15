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
    summary_more: "Plus a continuation worth expanding for.",
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

  it("note-toggle button reveals the textarea on click", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/profile/status": { has_profile: true },
        "/api/feed": FEED_SEED,
      }),
    );
    const el = await renderToday();
    document.body.appendChild(el);
    const notes = document.querySelector(".feedback-notes") as HTMLTextAreaElement;
    expect(notes.classList.contains("hidden")).toBe(true);
    (document.querySelector(".note-toggle-btn") as HTMLButtonElement).click();
    expect(notes.classList.contains("hidden")).toBe(false);
    (document.querySelector(".note-toggle-btn") as HTMLButtonElement).click();
    expect(notes.classList.contains("hidden")).toBe(true);
  });

  // Note: toggling a previously-reacted item back to "none" is exercised in the
  // existing renderDigest suite; in the feed view, the item never shows up
  // pre-reacted, so a dedicated branch test here would duplicate that path.

  it("thumbs-down on an unreacted item fades the card out", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/profile/status": { has_profile: true },
        "/api/feed": FEED_SEED,
      }),
    );
    const el = await renderToday();
    document.body.appendChild(el);
    const before = document.querySelectorAll("article.item").length;
    const down = document.querySelector("article.item .thumb.down") as HTMLButtonElement;
    down.click();
    await vi.runAllTimersAsync();
    expect(document.querySelectorAll("article.item").length).toBe(before - 1);
  });

  it("renders hostname when source is empty, stripping www.", async () => {
    const seed = structuredClone(FEED_SEED);
    seed[0].source = "";
    seed[0].url = "https://www.example.com/path";
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/profile/status": { has_profile: true }, "/api/feed": seed }),
    );
    const el = await renderToday();
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("example.com");
    expect(document.body.textContent).not.toContain("www.example.com");
  });

  it("video items render a duration_min meta line", async () => {
    const seed = structuredClone(FEED_SEED);
    seed[0].type = "video";
    seed[0].duration_min = 12;
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/profile/status": { has_profile: true }, "/api/feed": seed }),
    );
    const el = await renderToday();
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("12 min");
  });

  it("summary 'show more' toggle reveals then hides the continuation", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/profile/status": { has_profile: true }, "/api/feed": FEED_SEED }),
    );
    const el = await renderToday();
    document.body.appendChild(el);
    const wrap = document.querySelector(".summary:has(.summary-more)") as HTMLElement;
    const cont = wrap.querySelector(".summary-more") as HTMLElement;
    const toggle = wrap.querySelector(".summary-toggle") as HTMLButtonElement;
    expect(cont.hidden).toBe(true);
    toggle.click();
    expect(cont.hidden).toBe(false);
    expect(toggle.textContent).toBe("show less");
    toggle.click();
    expect(cont.hidden).toBe(true);
    expect(toggle.textContent).toBe("show more");
  });

  it("omits the show-more toggle when the agent did not provide a continuation", async () => {
    const seed = structuredClone(FEED_SEED).slice(0, 1);
    seed[0].summary_more = null;
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/profile/status": { has_profile: true }, "/api/feed": seed }),
    );
    const el = await renderToday();
    document.body.appendChild(el);
    const wrap = document.querySelector(".summary") as HTMLElement;
    expect(wrap.querySelector(".summary-toggle")).toBeNull();
    expect(wrap.querySelector(".summary-more")).toBeNull();
  });

  it("toggling thumbs-down off (down→none) clears the notes field, no fade", async () => {
    // First click is up→down (fades + remove); to exercise the toggle-off
    // path we render a digest where the item is already 'down'.
    const seed: Digest = {
      ...SEED,
      items: [{ ...SEED.items[0], feedback: "down" }],
    };
    const fetchMock = vi.fn<(input: RequestInfo | URL) => Promise<Response>>(
      async () => new Response('{"status":"ok"}', { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    document.body.appendChild(renderDigest(structuredClone(seed)));
    const notes = document.querySelector(".feedback-notes") as HTMLTextAreaElement;
    notes.value = "some note";
    (document.querySelector(".thumb.down") as HTMLButtonElement).click();
    await vi.runAllTimersAsync();
    // No fade-remove (prev wasn't null), and the notes field was cleared.
    expect(notes.value).toBe("");
  });

  it("malformed URLs render an empty source line instead of crashing", async () => {
    const seed = structuredClone(FEED_SEED);
    seed[0].source = "";
    seed[0].url = "not-a-url";
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/profile/status": { has_profile: true }, "/api/feed": seed }),
    );
    const el = await renderToday();
    document.body.appendChild(el);
    // No exception thrown; the article renders.
    expect(document.querySelectorAll("article.item").length).toBe(2);
  });

  it("falls back to digest_date when an item has no run_started_at", async () => {
    const seed = structuredClone(FEED_SEED);
    seed[0].run_started_at = null;
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/profile/status": { has_profile: true }, "/api/feed": seed }),
    );
    const el = await renderToday();
    document.body.appendChild(el);
    // Without crashing, and the card has a date-key built from digest_date.
    const card = document.querySelector("article.item") as HTMLElement;
    expect(card.dataset.dateKey).toBeTruthy();
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
