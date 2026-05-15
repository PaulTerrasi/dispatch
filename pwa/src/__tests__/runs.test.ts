import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderRuns } from "../views/runs";

beforeEach(() => {
  document.body.innerHTML = "";
  location.hash = "";
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function stub(routes: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      for (const [path, body] of Object.entries(routes)) {
        if (url.startsWith(path)) {
          return new Response(JSON.stringify(body), { status: 200 });
        }
      }
      return new Response("[]", { status: 200 });
    }),
  );
}

describe("renderRuns", () => {
  it("shows empty state when there are no runs", async () => {
    stub({ "/api/runs": [] });
    const el = await renderRuns();
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("No runs recorded");
  });

  it("groups runs by calendar date and shows day headers", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "r1",
          kind: "curation",
          date: "2026-05-14",
          started_at: "2026-05-14T08:00:00+00:00",
          duration_seconds: 120,
          item_count: 3,
          tool_calls: 5,
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
        {
          run_id: "r2",
          kind: "reflection",
          date: "2026-05-13",
          started_at: "2026-05-13T09:00:00+00:00",
          duration_seconds: 12,
          item_count: 0,
          profile_patches: 1,
          sources_changed: 1,
          reflection_notes: "ok",
          triggering_feedback: { kind: "thumb", value: "up", item_id: "abc" },
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".run-group-header").length).toBe(2);
    expect(document.querySelectorAll(".run-card").length).toBe(2);
  });

  it("short error messages are passed through truncate untouched", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "ref-shorterr",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T10:00:00+00:00",
          duration_seconds: 1,
          exit_reason: "error",
          error: "boom",
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    const chip = document.querySelector(".run-card-chips") as HTMLElement;
    expect(chip.textContent).toContain("boom");
    expect(chip.textContent).not.toContain("…");
  });

  it("failed reflection without an error message renders 'failed' chip", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "ref-fail",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T10:00:00+00:00",
          duration_seconds: 1,
          exit_reason: "error",
          // error intentionally absent
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("failed");
  });

  it("plural forms render correctly for multiple patches and sources", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "ref-many",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T10:00:00+00:00",
          duration_seconds: 1,
          profile_patches: 3,
          sources_changed: 1,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("3 patches");
    // 1 source (singular) — also exercises the != 1 false branch.
    expect(document.body.textContent).toContain("1 source changed");
  });

  it("renders an error chip with truncated error text for failed reflections", async () => {
    const long = "x".repeat(200);
    stub({
      "/api/runs": [
        {
          run_id: "r-err",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T10:00:00+00:00",
          duration_seconds: 1,
          exit_reason: "error",
          error: long,
          triggering_feedback: { kind: "thumb", value: "down", item_id: "x" },
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    const chip = document.querySelector(".run-card-chips") as HTMLElement;
    expect(chip.textContent).toContain("…");
    expect(chip.textContent!.length).toBeLessThan(long.length);
  });

  it("renders 'no changes' chip on reflections without patches or source changes", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "ref-noop",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T10:00:00+00:00",
          duration_seconds: 1,
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "nothing to do",
          triggering_feedback: { kind: "chat", value: "", item_id: "" },
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("no changes");
  });

  it("renders patch/source chips with correct singular/plural forms", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "ref-1",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T10:00:00+00:00",
          duration_seconds: 1,
          profile_patches: 1,
          sources_changed: 2,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("1 patch");
    expect(document.body.textContent).not.toContain("1 patches");
    expect(document.body.textContent).toContain("2 sources changed");
  });

  it("clicking a run card navigates to its detail page", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "r1",
          kind: "curation",
          date: "2026-05-14",
          started_at: "2026-05-14T08:00:00+00:00",
          item_count: 1,
          tool_calls: 1,
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    (document.querySelector(".run-card") as HTMLElement).click();
    expect(location.hash).toBe("#/run/r1");
  });

  it("Enter or Space keypress on a card also navigates", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "r2",
          kind: "curation",
          date: "2026-05-14",
          started_at: "2026-05-14T08:00:00+00:00",
          item_count: 1,
          tool_calls: 1,
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    const card = document.querySelector(".run-card") as HTMLElement;
    card.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
    expect(location.hash).toBe("#/run/r2");

    location.hash = "";
    card.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(location.hash).toBe("#/run/r2");

    location.hash = "";
    card.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true }));
    expect(location.hash).toBe(""); // no nav on other keys
  });

  it("multiple runs on the same date land in one group bucket", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "r1",
          kind: "curation",
          date: "2026-05-14",
          started_at: "2026-05-14T08:00:00+00:00",
          item_count: 1,
          tool_calls: 1,
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
        {
          run_id: "r2",
          kind: "curation",
          date: "2026-05-14",
          started_at: "2026-05-14T09:00:00+00:00",
          item_count: 1,
          tool_calls: 1,
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".run-group-header").length).toBe(1);
    expect(document.querySelectorAll(".run-card").length).toBe(2);
  });

  it("trigger label renders chat text quoted and truncates beyond 80 chars", async () => {
    const long = "x".repeat(120);
    stub({
      "/api/runs": [
        {
          run_id: "ref-chat",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T08:00:00+00:00",
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: { kind: "chat", text: long },
        },
        {
          run_id: "ref-thumb",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T07:00:00+00:00",
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: { kind: "thumb", value: "up", item_id: "abc" },
        },
        {
          run_id: "ref-empty",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T06:00:00+00:00",
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    const triggers = Array.from(document.querySelectorAll(".run-card-trigger")).map(
      (t) => t.textContent,
    );
    // Long chat text is truncated to 80 chars + ellipsis.
    expect(triggers.some((t) => t!.startsWith('chat: "') && t!.endsWith('…"'))).toBe(true);
    // Thumb event is rendered as 'thumb up …' (or similar). Just confirm a non-empty trigger.
    expect(triggers.some((t) => t!.includes("thumb"))).toBe(true);
    // Null triggering_feedback falls back to "feedback event".
    expect(triggers).toContain("feedback event");
  });

  it("thumb triggers without value/item_id show '?' placeholders", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "ref-thumb-noval",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T08:00:00+00:00",
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: { kind: "thumb" }, // no value or item_id
        },
        {
          run_id: "ref-unknown-event",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T07:00:00+00:00",
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: {}, // no kind at all → falls back
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    const triggers = Array.from(document.querySelectorAll(".run-card-trigger")).map(
      (t) => t.textContent,
    );
    expect(triggers.some((t) => t === "thumb ? on ?")).toBe(true);
    expect(triggers.some((t) => t === "feedback event")).toBe(true);
  });

  it("trigger label falls back to the `kind` when chat text is missing", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "ref-kind",
          kind: "reflection",
          date: "2026-05-14",
          started_at: "2026-05-14T08:00:00+00:00",
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: { kind: "noisy" }, // unknown kind, no text/value
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    const trigger = document.querySelector(".run-card-trigger") as HTMLElement;
    expect(trigger.textContent).toBe("noisy");
  });

  it("falls back to run.date when started_at is missing for grouping/time", async () => {
    stub({
      "/api/runs": [
        {
          run_id: "r3",
          kind: "curation",
          date: "2026-05-12",
          // no started_at
          item_count: 0,
          tool_calls: 0,
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".run-group-header").length).toBe(1);
    // Time cell falls back to em-dash when no started_at.
    expect((document.querySelector(".run-card-time") as HTMLElement).textContent).toBe("—");
  });

  it("clicking a card without run_id is a no-op", async () => {
    stub({
      "/api/runs": [
        {
          // run_id missing
          kind: "curation",
          date: "2026-05-14",
          started_at: "2026-05-14T08:00:00+00:00",
          item_count: 0,
          tool_calls: 0,
          profile_patches: 0,
          sources_changed: 0,
          reflection_notes: "",
          triggering_feedback: null,
        },
      ],
    });
    const el = await renderRuns();
    document.body.appendChild(el);
    location.hash = "";
    (document.querySelector(".run-card") as HTMLElement).click();
    expect(location.hash).toBe("");
  });
});
