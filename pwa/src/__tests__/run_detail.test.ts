import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderRunDetail } from "../views/run_detail";

beforeEach(() => {
  document.body.innerHTML = "";
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function stub(routes: Record<string, unknown | { status: number }>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      for (const [path, body] of Object.entries(routes)) {
        if (url.startsWith(path)) {
          if (body && typeof body === "object" && "status" in body) {
            return new Response("", { status: (body as { status: number }).status });
          }
          return new Response(body === undefined ? "null" : JSON.stringify(body), {
            status: 200,
          });
        }
      }
      return new Response("null", { status: 200 });
    }),
  );
}

const CURATION_RUN = {
  run_id: "cur1",
  kind: "curation",
  date: "2026-05-14",
  started_at: "2026-05-14T08:00:00+00:00",
  duration_seconds: 312,
  tool_calls: 3,
  item_count: 2,
  profile_patches: 0,
  sources_changed: 0,
  reflection_notes: "",
  triggering_feedback: null,
  agent_notes: "two keepers",
  system_prompt: "SYSTEM",
  user_prompt: "USER",
  tool_log: [
    {
      ts: "2026-05-14T08:00:01+00:00",
      tool: "fetch_rss",
      args: { url: "https://example.com/feed", limit: 10 },
      outcome: "10 entries",
      thinking: "thinking long enough to truncate ".repeat(10),
    },
    {
      ts: "2026-05-14T08:00:30+00:00",
      tool: "submit_digest",
      args: { count: 2 },
      outcome: "ok",
    },
  ],
  items: [
    {
      id: "abc",
      type: "article",
      title: "An article",
      source: "Example",
      url: "https://example.com/a",
      summary: "",
    },
  ],
};

describe("renderRunDetail", () => {
  it("reflection run renders prompts when only the user prompt is present", async () => {
    stub({
      "/api/runs/ref-userp": {
        ...CURATION_RUN,
        run_id: "ref-userp",
        kind: "reflection",
        item_count: null,
        triggering_feedback: { kind: "chat", text: "hi" },
        system_prompt: "",
        user_prompt: "the user said: hi",
        reflection_notes: "",
        tool_log: [],
        items: [],
        exit_reason: "ok",
      },
    });
    const el = await renderRunDetail({ run_id: "ref-userp" });
    document.body.appendChild(el);
    // Toggle is present because the user_prompt half of the OR fired.
    expect(document.querySelector(".run-prompt-toggle")).not.toBeNull();
  });

  it("curation run renders prompts when only the system prompt is present", async () => {
    stub({
      "/api/runs/cur-sysp": {
        ...CURATION_RUN,
        run_id: "cur-sysp",
        system_prompt: "you are the curator",
        user_prompt: "",
        tool_log: [],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "cur-sysp" });
    document.body.appendChild(el);
    expect(document.querySelector(".run-prompt-toggle")).not.toBeNull();
  });

  it("curation run renders prompts when only the user prompt is present", async () => {
    stub({
      "/api/runs/cur-userp": {
        ...CURATION_RUN,
        run_id: "cur-userp",
        system_prompt: "",
        user_prompt: "curate today",
        tool_log: [],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "cur-userp" });
    document.body.appendChild(el);
    expect(document.querySelector(".run-prompt-toggle")).not.toBeNull();
  });

  it("reflection run renders prompts when only the system prompt is present", async () => {
    stub({
      "/api/runs/ref-sysp": {
        ...CURATION_RUN,
        run_id: "ref-sysp",
        kind: "reflection",
        item_count: null,
        triggering_feedback: { kind: "chat", text: "hi" },
        system_prompt: "you are the reflector",
        user_prompt: "",
        reflection_notes: "",
        tool_log: [],
        items: [],
        exit_reason: "ok",
      },
    });
    const el = await renderRunDetail({ run_id: "ref-sysp" });
    document.body.appendChild(el);
    expect(document.querySelector(".run-prompt-toggle")).not.toBeNull();
  });

  it("renders summary dashes when duration and tool_calls are null", async () => {
    stub({
      "/api/runs/sparse": {
        ...CURATION_RUN,
        run_id: "sparse",
        duration_seconds: null,
        tool_calls: null,
        item_count: null,
        tool_log: [],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "sparse" });
    document.body.appendChild(el);
    const row = document.querySelector(".run-summary-row") as HTMLElement;
    // Two em-dashes: one for duration, one for tool calls.
    expect((row.textContent!.match(/—/g) || []).length).toBe(2);
  });

  it("treats a missing run_id param as an empty string", async () => {
    stub({ "/api/runs/": null });
    const el = await renderRunDetail({});
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("No run found");
  });

  it('renders an empty "not found" state when the API returns null', async () => {
    stub({ "/api/runs/missing": null });
    const el = await renderRunDetail({ run_id: "missing" });
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("No run found for missing");
  });

  it("renders a curation run's summary row, prompt block, and item list", async () => {
    stub({ "/api/runs/cur1": CURATION_RUN });
    const el = await renderRunDetail({ run_id: "cur1" });
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("Curation");
    expect(document.body.textContent).toContain("two keepers");
    expect(document.body.textContent).toContain("An article");
    // Tool log entries with thinking are rendered with a THK badge.
    expect(document.body.textContent).toContain("THK");
    // Items submitted phase label.
    expect(document.body.textContent).toContain("Items submitted");
  });

  it("toggles the prompt block on click", async () => {
    stub({ "/api/runs/cur1": CURATION_RUN });
    const el = await renderRunDetail({ run_id: "cur1" });
    document.body.appendChild(el);
    const toggle = document.querySelector(".run-prompt-toggle") as HTMLButtonElement;
    const body = document.querySelector(".run-prompt-body") as HTMLElement;
    expect(body.classList.contains("collapsed")).toBe(true);
    toggle.click();
    expect(body.classList.contains("collapsed")).toBe(false);
    expect(toggle.textContent).toBe("Hide");
    toggle.click();
    expect(body.classList.contains("collapsed")).toBe(true);
    expect(toggle.textContent).toBe("Show");
  });

  it("toggles the thinking row's expand/collapse state", async () => {
    stub({ "/api/runs/cur1": CURATION_RUN });
    const el = await renderRunDetail({ run_id: "cur1" });
    document.body.appendChild(el);
    const preview = document.querySelector(".run-think-preview") as HTMLElement;
    const full = document.querySelector(".run-think-full") as HTMLElement;
    const btn = document.querySelector(".run-note-toggle-btn") as HTMLButtonElement;
    expect(full.classList.contains("collapsed")).toBe(true);
    btn.click();
    expect(full.classList.contains("collapsed")).toBe(false);
    expect(preview.hidden).toBe(true);
    btn.click();
    expect(full.classList.contains("collapsed")).toBe(true);
  });

  it("renders a reflection run's triggering feedback and reflection note", async () => {
    stub({
      "/api/runs/ref1": {
        ...CURATION_RUN,
        run_id: "ref1",
        kind: "reflection",
        item_count: null,
        profile_patches: 1,
        sources_changed: 1,
        reflection_notes: "noted woodworking interest",
        triggering_feedback: { kind: "thumb", value: "up", item_id: "abc" },
        agent_notes: "",
        items: [],
        exit_reason: "ok",
      },
    });
    const el = await renderRunDetail({ run_id: "ref1" });
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("Triggering feedback");
    expect(document.body.textContent).toContain("noted woodworking interest");
    expect(document.body.textContent).toContain("Profile patches");
    expect(document.body.textContent).toContain("Sources changed");
  });

  it("surfaces the error message on a failed reflection", async () => {
    stub({
      "/api/runs/ref-err": {
        ...CURATION_RUN,
        run_id: "ref-err",
        kind: "reflection",
        item_count: null,
        triggering_feedback: { kind: "thumb", value: "down", item_id: "abc" },
        exit_reason: "error",
        error: "RuntimeError: Command failed with exit code 1",
        tool_log: [],
        items: [],
        agent_notes: "",
      },
    });
    const el = await renderRunDetail({ run_id: "ref-err" });
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("Error");
    expect(document.body.textContent).toContain("exit code 1");
  });

  it("renders the run's date when started_at is null", async () => {
    stub({
      "/api/runs/no-ts": {
        ...CURATION_RUN,
        run_id: "no-ts",
        started_at: null,
        tool_log: [],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "no-ts" });
    document.body.appendChild(el);
    // Title falls back to formatDate(run.date).
    const title = document.querySelector(".page-title") as HTMLElement;
    expect(title.textContent).toContain("2026");
  });

  it("clamps long notes and the Show more / Show less toggle works", async () => {
    const longNotes = "line\n".repeat(40).trim();
    stub({
      "/api/runs/long-notes": {
        ...CURATION_RUN,
        run_id: "long-notes",
        agent_notes: longNotes,
        tool_log: [],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "long-notes" });
    document.body.appendChild(el);
    const note = document.querySelector(".run-note-body") as HTMLElement;
    expect(note.classList.contains("clamped")).toBe(true);
    const btn = note.parentElement!.querySelector(".run-note-toggle-btn") as HTMLButtonElement;
    btn.click();
    expect(note.classList.contains("clamped")).toBe(false);
    expect(btn.textContent).toBe("Show less");
    btn.click();
    expect(note.classList.contains("clamped")).toBe(true);
    expect(btn.textContent).toBe("Show more");
  });

  it("renders multiple badge categories for varied tools", async () => {
    stub({
      "/api/runs/many": {
        ...CURATION_RUN,
        run_id: "many",
        tool_log: [
          { ts: "t", tool: "fetch_rss", args: { url: "https://x" }, outcome: "ok" },
          { ts: "t", tool: "fetch_youtube_channel", args: {}, outcome: "ok" },
          { ts: "t", tool: "fetch_youtube_transcript", args: { video_id: "x" }, outcome: "ok" },
          { ts: "t", tool: "web_fetch", args: { url: "https://x.example/a" }, outcome: "ok" },
          { ts: "t", tool: "WebSearch", args: { q: "x" }, outcome: "ok" },
          { ts: "t", tool: "WebFetch", args: { url: "https://x.example/b" }, outcome: "ok" },
          { ts: "t", tool: "read_recent_feedback", args: { days: 14 }, outcome: "0" },
          { ts: "t", tool: "list_sources", args: {}, outcome: "0" },
          { ts: "t", tool: "submit_digest", args: { count: 0 }, outcome: "ok" },
          { ts: "t", tool: "end_reflection", args: { notes: "n" }, outcome: "ok" },
          { ts: "t", tool: "patch_profile", args: {}, outcome: "ok" },
          { ts: "t", tool: "add_source", args: {}, outcome: "ok" },
          { ts: "t", tool: "remove_source", args: {}, outcome: "ok" },
          { ts: "t", tool: "unknown_tool", args: {}, outcome: "ok" },
        ],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "many" });
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("RSS");
    expect(document.body.textContent).toContain("YT");
    expect(document.body.textContent).toContain("WEB");
    expect(document.body.textContent).toContain("SRH");
    expect(document.body.textContent).toContain("READ");
    expect(document.body.textContent).toContain("LIST");
    expect(document.body.textContent).toContain("SUB");
    expect(document.body.textContent).toContain("END");
    expect(document.body.textContent).toContain("PAT");
    expect(document.body.textContent).toContain("ADD");
    expect(document.body.textContent).toContain("REM");
    // Unknown tool gets the first 3 chars uppercased.
    expect(document.body.textContent).toContain("UNK");
  });

  it("renders an expandable details block per tool call when details are present", async () => {
    stub({
      "/api/runs/details": {
        ...CURATION_RUN,
        run_id: "details",
        tool_log: [
          {
            ts: "t",
            tool: "read_profile",
            args: {},
            outcome: "12 chars",
            details: { profile: "# Profile\nHello." },
          },
          {
            ts: "t",
            tool: "fetch_rss",
            args: { url: "https://example.com/feed" },
            outcome: "2 entries",
            details: {
              entries: [
                { title: "Post A", url: "https://example.com/a", source: "Example" },
                { title: "Post B", url: "https://example.com/b" },
              ],
            },
          },
          {
            ts: "t",
            tool: "submit_digest",
            args: { count: 1 },
            outcome: "ok",
            details: {
              items: [{ title: "Picked", url: "https://example.com/a" }],
              agent_notes: "kept one",
            },
          },
        ],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "details" });
    document.body.appendChild(el);
    const toggles = document.querySelectorAll(".run-event-details-toggle");
    expect(toggles.length).toBe(3);
    // Body starts collapsed.
    const firstBody = document.querySelector(".run-event-details-body") as HTMLElement;
    expect(firstBody.classList.contains("collapsed")).toBe(true);
    (toggles[0] as HTMLButtonElement).click();
    expect(firstBody.classList.contains("collapsed")).toBe(false);
    expect(firstBody.textContent).toContain("# Profile");
    expect((toggles[0] as HTMLButtonElement).textContent).toBe("Hide details");
    // Toggling back returns to the collapsed state.
    (toggles[0] as HTMLButtonElement).click();
    expect(firstBody.classList.contains("collapsed")).toBe(true);
    expect((toggles[0] as HTMLButtonElement).textContent).toBe("Show details");
    (toggles[0] as HTMLButtonElement).click();
    // RSS entries render as a list of titles with linked URLs.
    (toggles[1] as HTMLButtonElement).click();
    const rssBody = toggles[1].nextElementSibling as HTMLElement;
    const links = rssBody.querySelectorAll(".run-event-detail-row-title a");
    expect(Array.from(links).map((a) => a.textContent)).toEqual(["Post A", "Post B"]);
    // submit_digest exposes both items and agent_notes sections.
    (toggles[2] as HTMLButtonElement).click();
    expect(document.body.textContent).toContain("kept one");
  });

  it("renders detail values that are arrays of primitives, plain objects, and primitives as JSON", async () => {
    stub({
      "/api/runs/shapes": {
        ...CURATION_RUN,
        run_id: "shapes",
        tool_log: [
          {
            ts: "t",
            tool: "read_recent_curation_runs",
            args: { days: 7 },
            outcome: "2 runs",
            details: {
              run_ids: ["run-a", "run-b"], // array of strings → JSON pre
              triggering_event: { kind: "thumb", value: "up" }, // plain object → JSON pre
              rejected: true, // primitive → stringified
            },
          },
        ],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "shapes" });
    document.body.appendChild(el);
    (document.querySelector(".run-event-details-toggle") as HTMLButtonElement).click();
    const text = document.body.textContent ?? "";
    expect(text).toContain('"run-a"');
    expect(text).toContain('"kind": "thumb"');
    expect(text).toContain("true");
  });

  it("filters out null/empty detail entries and skips empty details blocks", async () => {
    stub({
      "/api/runs/empty-details": {
        ...CURATION_RUN,
        run_id: "empty-details",
        tool_log: [
          {
            ts: "t",
            tool: "read_profile",
            args: {},
            outcome: "ok",
            // All values filtered out (null, empty string, empty array) → no toggle.
            details: { profile: null, notes: "", entries: [] },
          },
          {
            ts: "t",
            tool: "read_profile",
            args: {},
            outcome: "ok",
            // Mix of filtered and kept — kept ones still appear.
            details: { profile: "kept", entries: [], rejected: null },
          },
        ],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "empty-details" });
    document.body.appendChild(el);
    // Only the second entry has non-empty details, so exactly one toggle exists.
    const toggles = document.querySelectorAll(".run-event-details-toggle");
    expect(toggles.length).toBe(1);
    (toggles[0] as HTMLButtonElement).click();
    expect(document.body.textContent).toContain("kept");
  });

  it("skips non-http(s) URLs in detail rows (no javascript: hrefs)", async () => {
    stub({
      "/api/runs/xss": {
        ...CURATION_RUN,
        run_id: "xss",
        tool_log: [
          {
            ts: "t",
            tool: "fetch_rss",
            args: { url: "https://x" },
            outcome: "2 entries",
            details: {
              entries: [
                { title: "Evil", url: "javascript:alert(1)" },
                { title: "Plain title only" },
                { source: "sub-only" },
                { id: "no-title-no-sub" },
              ],
              unknown_detail_key: "free-form value",
            },
          },
        ],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "xss" });
    document.body.appendChild(el);
    (document.querySelector(".run-event-details-toggle") as HTMLButtonElement).click();
    // No anchors rendered: the only "URL" was a javascript: scheme, dropped.
    expect(document.querySelectorAll(".run-event-detail-row-title a").length).toBe(0);
    // Title-only and sub-only rows still render their text.
    const titles = document.querySelectorAll(".run-event-detail-row-title");
    expect(titles[0].textContent).toBe("Evil");
    expect(titles[1].textContent).toBe("Plain title only");
    expect(titles[2].textContent).toBe("sub-only");
    // No title and no sub → fall back to the row's JSON.
    expect(titles[3].textContent).toContain("no-title-no-sub");
    // Unknown detail key falls through to the key name itself as the label.
    expect(document.body.textContent).toContain("unknown_detail_key");
  });

  it("maps legacy flat profile_snapshot into details on the read_profile entry", async () => {
    stub({
      "/api/runs/legacy": {
        ...CURATION_RUN,
        run_id: "legacy",
        tool_log: [
          {
            ts: "t",
            tool: "read_profile",
            args: {},
            outcome: "10 chars",
            details: { profile_snapshot: "old profile text" },
          },
        ],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "legacy" });
    document.body.appendChild(el);
    const toggle = document.querySelector(".run-event-details-toggle") as HTMLButtonElement;
    toggle.click();
    expect(document.body.textContent).toContain("old profile text");
  });

  it("reflection run without prompts or notes still renders cleanly", async () => {
    stub({
      "/api/runs/ref-bare": {
        ...CURATION_RUN,
        run_id: "ref-bare",
        kind: "reflection",
        item_count: null,
        triggering_feedback: { kind: "chat", text: "hi" },
        system_prompt: "",
        user_prompt: "",
        reflection_notes: "",
        tool_log: [],
        items: [],
        exit_reason: "ok",
      },
    });
    const el = await renderRunDetail({ run_id: "ref-bare" });
    document.body.appendChild(el);
    // The triggering-feedback block reuses .run-prompt-block; the prompt
    // block proper has a toggle and a labeled body. Detect it by toggle.
    expect(document.querySelector(".run-prompt-toggle")).toBeNull();
  });

  it("curation run without prompts or notes still renders cleanly", async () => {
    stub({
      "/api/runs/bare": {
        ...CURATION_RUN,
        run_id: "bare",
        system_prompt: "",
        user_prompt: "",
        agent_notes: "",
        tool_log: [],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "bare" });
    document.body.appendChild(el);
    // No prompt block when both prompts are empty.
    expect(document.querySelector(".run-prompt-block")).toBeNull();
  });

  it("thinking ≤80 chars renders without an ellipsis in the preview", async () => {
    stub({
      "/api/runs/short-think": {
        ...CURATION_RUN,
        run_id: "short-think",
        tool_log: [
          {
            ts: "t",
            tool: "read_profile",
            args: {},
            outcome: "ok",
            thinking: "short thought",
          },
        ],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "short-think" });
    document.body.appendChild(el);
    const preview = document.querySelector(".run-think-preview") as HTMLElement;
    expect(preview.textContent).toBe("short thought");
    expect(preview.textContent).not.toContain("…");
  });

  it("durations under a minute render as a single seconds count", async () => {
    stub({
      "/api/runs/quick": {
        ...CURATION_RUN,
        run_id: "quick",
        duration_seconds: 12,
        tool_log: [],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "quick" });
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("12s");
    expect(document.body.textContent).not.toMatch(/\d+m \d+s/);
  });

  it("renders no triggering-feedback block when the reflection has no event", async () => {
    stub({
      "/api/runs/ref-noev": {
        ...CURATION_RUN,
        run_id: "ref-noev",
        kind: "reflection",
        item_count: null,
        triggering_feedback: null,
        exit_reason: "ok",
        tool_log: [],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "ref-noev" });
    document.body.appendChild(el);
    expect(document.body.textContent).not.toContain("Triggering feedback");
  });

  it("formats condensed args: hostnames for URLs, arrays/objects, long strings", async () => {
    stub({
      "/api/runs/args": {
        ...CURATION_RUN,
        run_id: "args",
        tool_log: [
          {
            ts: "t",
            tool: "web_fetch",
            args: {
              url: "https://hostname.example.com/some/long/path",
              not_a_url: "http://[",
              tags: ["a", "b", "c"],
              meta: { k: "v" },
              ignored: null,
              short: "short value",
              long: "x".repeat(60),
            },
            outcome: "ok",
          },
        ],
        items: [],
      },
    });
    const el = await renderRunDetail({ run_id: "args" });
    document.body.appendChild(el);
    const argsCell = document.querySelector(".run-event-args") as HTMLElement;
    expect(argsCell.textContent).toContain("url=hostname.example.com");
    // A string that starts with http but fails URL parsing falls back to raw.
    expect(argsCell.textContent).toContain("not_a_url=http://[");
    expect(argsCell.textContent).toContain("tags=[3]");
    expect(argsCell.textContent).toContain("meta={…}");
    expect(argsCell.textContent).not.toContain("ignored=");
    expect(argsCell.textContent).toContain("short=short value");
    expect(argsCell.textContent).toContain("…");
  });
});
