import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatStreamHandlers } from "../api";
import { renderChat } from "../views/chat";

const TURNS_KEY = "chat:turns";
const COLLAPSED_KEY = "chat:profile-collapsed";

beforeEach(() => {
  document.body.innerHTML = "";
  document.body.classList.remove("chat-page");
  localStorage.clear();
  location.hash = "";
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function stubProfileFetch(markdown = "# Profile\n\n## Standing interests\n- LLMs\n") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.startsWith("/api/profile")) {
        return new Response(JSON.stringify({ markdown }), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    }),
  );
}

describe("renderChat — layout + lifecycle", () => {
  it("sets the chat-page body class while the chat view is mounted", async () => {
    stubProfileFetch();
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.body.classList.contains("chat-page")).toBe(true);
    // Leaving the route fires hashchange, which removes the class.
    location.hash = "#/today";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(document.body.classList.contains("chat-page")).toBe(false);
  });

  it("renders header, transcript intro, input row, and profile panel", async () => {
    stubProfileFetch();
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.querySelector(".chat-toolbar")).not.toBeNull();
    expect(document.querySelector(".chat-transcript .note")).not.toBeNull();
    expect(document.querySelector(".chat-input")).not.toBeNull();
    expect(document.querySelector(".chat-profile")).not.toBeNull();
  });

  it("profile panel populates from /api/profile and renders markdown", async () => {
    stubProfileFetch("# Welcome\n\n## Interests\n- woodworking\n");
    const el = await renderChat();
    document.body.appendChild(el);
    await new Promise((r) => setTimeout(r, 5)); // let refreshProfile() resolve
    const body = document.querySelector(".chat-profile-body") as HTMLElement;
    expect(body.innerHTML).toContain("<h2>Welcome</h2>");
    expect(body.innerHTML).toContain("<h3>Interests</h3>");
    expect(body.innerHTML).toContain("<li>woodworking</li>");
  });

  it("profile fetch failure surfaces an error string in the panel", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }),
    );
    const el = await renderChat();
    document.body.appendChild(el);
    await new Promise((r) => setTimeout(r, 5));
    expect((document.querySelector(".chat-profile-body") as HTMLElement).textContent).toContain(
      "could not load profile",
    );
  });

  it("clicking the profile header toggles its collapsed state and persists", async () => {
    stubProfileFetch();
    const el = await renderChat();
    document.body.appendChild(el);
    const layout = document.querySelector(".chat-layout") as HTMLElement;
    const header = document.querySelector(".chat-profile-header") as HTMLButtonElement;

    // Default: collapsed.
    expect(layout.classList.contains("profile-collapsed")).toBe(true);
    header.click();
    expect(layout.classList.contains("profile-collapsed")).toBe(false);
    expect(localStorage.getItem(COLLAPSED_KEY)).toBe("0");
    header.click();
    expect(layout.classList.contains("profile-collapsed")).toBe(true);
    expect(localStorage.getItem(COLLAPSED_KEY)).toBe("1");
  });

  it("clicking the backdrop collapses the profile panel", async () => {
    stubProfileFetch();
    localStorage.setItem(COLLAPSED_KEY, "0"); // start expanded
    const el = await renderChat();
    document.body.appendChild(el);
    const layout = document.querySelector(".chat-layout") as HTMLElement;
    expect(layout.classList.contains("profile-collapsed")).toBe(false);
    (document.querySelector(".chat-profile-backdrop") as HTMLElement).click();
    expect(layout.classList.contains("profile-collapsed")).toBe(true);
  });
});

// ── Replay from localStorage ────────────────────────────────────────────────

describe("renderChat — loadItems / persistItems", () => {
  it("replays normalized msg items", async () => {
    stubProfileFetch();
    localStorage.setItem(
      TURNS_KEY,
      JSON.stringify([
        { kind: "msg", role: "user", text: "hello" },
        { kind: "msg", role: "assistant", text: "hi back" },
      ]),
    );
    const el = await renderChat();
    document.body.appendChild(el);
    const msgs = document.querySelectorAll(".chat-msg");
    expect(msgs.length).toBe(2);
    expect(msgs[0].textContent).toBe("hello");
    expect(msgs[1].textContent).toBe("hi back");
  });

  it("normalizes legacy entries lacking `kind`", async () => {
    stubProfileFetch();
    localStorage.setItem(TURNS_KEY, JSON.stringify([{ role: "user", text: "legacy" }]));
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".chat-msg")[0].textContent).toBe("legacy");
  });

  it("normalizes a legacy assistant-role entry", async () => {
    stubProfileFetch();
    localStorage.setItem(
      TURNS_KEY,
      JSON.stringify([{ role: "assistant", text: "hi from old build" }]),
    );
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".chat-msg")[0].textContent).toBe("hi from old build");
  });

  it("rejects a legacy-shaped entry with the wrong text type", async () => {
    stubProfileFetch();
    localStorage.setItem(
      TURNS_KEY,
      JSON.stringify([{ role: "user", text: 42 }]), // text isn't a string
    );
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".chat-msg").length).toBe(0);
  });

  it("replays tool items with their status class", async () => {
    stubProfileFetch();
    localStorage.setItem(
      TURNS_KEY,
      JSON.stringify([
        { kind: "tool", id: "t1", label: "running", status: "pending" },
        { kind: "tool", id: "t2", label: "ok!", status: "ok" },
        { kind: "tool", id: "t3", label: "boom", status: "err" },
      ]),
    );
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".chat-tool-card.ok").length).toBe(1);
    expect(document.querySelectorAll(".chat-tool-card.err").length).toBe(1);
    expect(document.querySelectorAll(".chat-tool-card.pending").length).toBe(1);
  });

  it("replays full-shape tool items (with name/input/output) and shows both sections on expand", async () => {
    stubProfileFetch();
    localStorage.setItem(
      TURNS_KEY,
      JSON.stringify([
        {
          kind: "tool",
          id: "t9",
          name: "patch_profile",
          label: "editing profile.md",
          input: { diff: "abc" },
          output: "applied",
          status: "ok",
        },
        // String input exercises the typeof-string fast path in formatToolInput.
        {
          kind: "tool",
          id: "t10",
          name: "add_source",
          label: "adding source",
          input: "raw string input",
          status: "ok",
        },
      ]),
    );
    document.body.appendChild(await renderChat());
    const cards = document.querySelectorAll(".chat-tool-card");
    expect(cards.length).toBe(2);
    // Expand first card and verify both sections are present.
    (cards[0].querySelector(".chat-tool-card-header") as HTMLButtonElement).click();
    const labels = Array.from(cards[0].querySelectorAll(".chat-tool-card-section-label")).map(
      (l) => l.textContent,
    );
    expect(labels).toEqual(["Input", "Output"]);
    expect(cards[0].textContent).toContain("applied");
    // Expand second card — string input is shown verbatim.
    (cards[1].querySelector(".chat-tool-card-header") as HTMLButtonElement).click();
    const pre = cards[1].querySelector(".chat-tool-card-section-content") as HTMLElement;
    expect(pre.textContent).toBe("raw string input");
  });

  it("replays reasoning items as collapsible cards", async () => {
    stubProfileFetch();
    localStorage.setItem(TURNS_KEY, JSON.stringify([{ kind: "reasoning", text: "deep thoughts" }]));
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".chat-reasoning").length).toBe(1);
    expect((document.querySelector(".chat-reasoning-body") as HTMLElement).textContent).toBe(
      "deep thoughts",
    );
  });

  it("ignores malformed entries (non-objects, bad kinds, wrong types)", async () => {
    stubProfileFetch();
    localStorage.setItem(
      TURNS_KEY,
      JSON.stringify([
        null,
        42,
        { kind: "msg", role: "bogus", text: "x" },
        { kind: "tool", id: 1, label: "x", status: "ok" }, // id not a string
        { kind: "tool", id: "y", label: "x", status: "unknown" },
        { kind: "reasoning", text: 5 }, // text not a string
        { kind: "msg", role: "user", text: "kept" },
      ]),
    );
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".chat-msg").length).toBe(1);
    expect(document.querySelectorAll(".chat-tool-card").length).toBe(0);
    expect(document.querySelectorAll(".chat-reasoning").length).toBe(0);
  });

  it("handles non-array JSON in localStorage by starting empty", async () => {
    stubProfileFetch();
    localStorage.setItem(TURNS_KEY, JSON.stringify({ not: "an array" }));
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".chat-msg").length).toBe(0);
  });

  it("handles invalid JSON in localStorage by starting empty", async () => {
    stubProfileFetch();
    localStorage.setItem(TURNS_KEY, "not-json");
    const el = await renderChat();
    document.body.appendChild(el);
    expect(document.querySelectorAll(".chat-msg").length).toBe(0);
  });
});

// ── Send flow with a fake chatStream ───────────────────────────────────────

describe("renderChat — send / reset / streaming", () => {
  /**
   * Build a fresh chat view with a fake `api.chatStream` that captures the
   * handlers it receives, so we can drive SSE events from the test side.
   */
  async function mountWithFakeStream(): Promise<{
    capturedHandlers: ChatStreamHandlers[];
    abortCalls: number;
    el: HTMLElement;
  }> {
    const capturedHandlers: ChatStreamHandlers[] = [];
    let abortCalls = 0;

    vi.resetModules();
    vi.doMock("../api", async () => {
      const realModule = await vi.importActual<typeof import("../api")>("../api");
      return {
        ...realModule,
        api: {
          ...realModule.api,
          profile: async () => ({ markdown: "# P\n" }),
          chatStream: (
            _history: { role: "user" | "assistant"; text: string }[],
            handlers: ChatStreamHandlers,
          ) => {
            capturedHandlers.push(handlers);
            return {
              abort: () => {
                abortCalls++;
              },
              signal: { aborted: false } as AbortSignal,
            } as unknown as AbortController;
          },
        },
      };
    });

    const { renderChat: rc } = await import("../views/chat");
    const el = await rc();
    document.body.appendChild(el);
    await new Promise((r) => setTimeout(r, 0));
    return {
      capturedHandlers,
      get abortCalls() {
        return abortCalls;
      },
      el,
    };
  }

  it("Send appends only the user message until text actually streams", async () => {
    const { capturedHandlers } = await mountWithFakeStream();
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    const send = document.querySelector(".chat-input .primary") as HTMLButtonElement;
    ta.value = "Hi!";
    send.click();

    // No empty assistant placeholder — just the user message + a loading indicator.
    expect(document.querySelectorAll(".chat-msg").length).toBe(1);
    expect(document.querySelectorAll(".chat-loading").length).toBe(1);
    expect(send.disabled).toBe(true);

    const handlers = capturedHandlers.at(-1)!;
    handlers.onText?.("Hello ");
    // First delta creates the assistant bubble and hides the loader.
    expect(document.querySelectorAll(".chat-msg").length).toBe(2);
    expect(document.querySelectorAll(".chat-loading").length).toBe(0);
    expect(document.querySelectorAll(".chat-msg.streaming").length).toBe(1);
    handlers.onText?.("there.");
    const assistant = document.querySelectorAll(".chat-msg")[1];
    expect(assistant.textContent).toBe("Hello there.");
    handlers.onDone?.();
    // Streaming cursor cleared after done.
    expect(document.querySelectorAll(".chat-msg.streaming").length).toBe(0);
    expect(send.disabled).toBe(false);
    // Persisted to localStorage.
    const persisted = JSON.parse(localStorage.getItem(TURNS_KEY)!);
    expect(persisted.some((it: { text?: string }) => it.text === "Hello there.")).toBe(true);
  });

  it("Tool calls before any text do not leave an empty assistant bubble", async () => {
    const { capturedHandlers } = await mountWithFakeStream();
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "edit profile";
    (document.querySelector(".chat-input .primary") as HTMLButtonElement).click();
    const handlers = capturedHandlers.at(-1)!;
    handlers.onToolStart?.({ id: "t1", name: "patch_profile", input: { diff: "..." } });
    handlers.onToolEnd?.({ tool_use_id: "t1", ok: true, output: "applied" });
    // Only the user message — no orphaned assistant bubble above the tool card.
    expect(document.querySelectorAll(".chat-msg").length).toBe(1);
    expect(document.querySelectorAll(".chat-tool-card").length).toBe(1);
    // Loader still visible between tool end and the next event.
    expect(document.querySelectorAll(".chat-loading").length).toBe(1);
    handlers.onText?.("All done.");
    expect(document.querySelectorAll(".chat-msg").length).toBe(2);
    expect(document.querySelectorAll(".chat-loading").length).toBe(0);
  });

  it("Empty input does not send", async () => {
    const { capturedHandlers } = await mountWithFakeStream();
    const send = document.querySelector(".chat-input .primary") as HTMLButtonElement;
    send.click();
    expect(capturedHandlers.length).toBe(0);
  });

  it("Enter sends, Shift+Enter does not", async () => {
    const { capturedHandlers } = await mountWithFakeStream();
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "go";
    ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(capturedHandlers.length).toBe(1);

    ta.value = "shift";
    ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true }));
    expect(capturedHandlers.length).toBe(1);
  });

  it("Tool start renders a collapsible card; tool_end flips its status class and exposes output", async () => {
    const { capturedHandlers } = await mountWithFakeStream();
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "edit profile";
    (document.querySelector(".chat-input .primary") as HTMLButtonElement).click();
    const handlers = capturedHandlers.at(-1)!;

    handlers.onToolStart?.({ id: "t1", name: "patch_profile", input: { diff: "abc" } });
    handlers.onToolStart?.({ id: "t2", name: "unknown_tool", input: {} });
    expect(document.querySelectorAll(".chat-tool-card").length).toBe(2);
    // Pending state shows the "running…" status label.
    expect(document.querySelectorAll(".chat-tool-card.pending").length).toBe(2);

    handlers.onToolEnd?.({ tool_use_id: "t1", ok: true, output: "applied diff" });
    handlers.onToolEnd?.({ tool_use_id: "t2", ok: false });
    // Unknown tool_use_id gracefully ignored.
    handlers.onToolEnd?.({ tool_use_id: "missing", ok: true });
    expect(document.querySelectorAll(".chat-tool-card.ok").length).toBe(1);
    expect(document.querySelectorAll(".chat-tool-card.err").length).toBe(1);

    // Header click toggles the .expanded class so the body shows.
    const okCard = document.querySelector(".chat-tool-card.ok") as HTMLElement;
    const okHeader = okCard.querySelector(".chat-tool-card-header") as HTMLButtonElement;
    expect(okCard.classList.contains("expanded")).toBe(false);
    okHeader.click();
    expect(okCard.classList.contains("expanded")).toBe(true);
    // Expanded body shows both Input and Output sections.
    const sections = okCard.querySelectorAll(".chat-tool-card-section-label");
    const labels = Array.from(sections).map((s) => s.textContent);
    expect(labels).toContain("Input");
    expect(labels).toContain("Output");
    expect(okCard.textContent).toContain("applied diff");
  });

  it("Reasoning deltas render a streaming reasoning card and finalize on next event", async () => {
    const { capturedHandlers } = await mountWithFakeStream();
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "go";
    (document.querySelector(".chat-input .primary") as HTMLButtonElement).click();
    const handlers = capturedHandlers.at(-1)!;
    handlers.onReasoning?.("Pondering ");
    handlers.onReasoning?.("the question.");
    expect(document.querySelectorAll(".chat-reasoning").length).toBe(1);
    const card = document.querySelector(".chat-reasoning") as HTMLElement;
    expect(card.classList.contains("streaming")).toBe(true);
    const body = card.querySelector(".chat-reasoning-body") as HTMLElement;
    expect(body.textContent).toBe("Pondering the question.");
    // Header click toggles the persistent expansion.
    (card.querySelector(".chat-reasoning-header") as HTMLButtonElement).click();
    expect(card.classList.contains("expanded")).toBe(true);
    // A text delta finalizes the reasoning card and starts an assistant bubble.
    handlers.onText?.("Answer.");
    expect(card.classList.contains("streaming")).toBe(false);
    expect(document.querySelectorAll(".chat-msg").length).toBe(2);
  });

  it("ProfileChanged triggers a profile refetch", async () => {
    const fetchedAfterStart: string[] = [];
    vi.resetModules();
    vi.doMock("../api", async () => {
      const real = await vi.importActual<typeof import("../api")>("../api");
      return {
        ...real,
        api: {
          ...real.api,
          profile: async () => {
            fetchedAfterStart.push("call");
            return { markdown: "# P\n" };
          },
          chatStream: (
            _h: { role: "user" | "assistant"; text: string }[],
            handlers: ChatStreamHandlers,
          ) => {
            // Capture and fire profile_changed immediately.
            queueMicrotask(() => handlers.onProfileChanged?.("add_source"));
            return { abort: () => {} } as unknown as AbortController;
          },
        },
      };
    });
    const { renderChat: rc } = await import("../views/chat");
    document.body.appendChild(await rc());
    await new Promise((r) => setTimeout(r, 0));
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "add foo";
    (document.querySelector(".chat-input .primary") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 5));
    expect(fetchedAfterStart.length).toBeGreaterThanOrEqual(2); // initial + after stream
  });

  it("Stream error shows an error banner, clears the loader, and re-enables Send", async () => {
    const { capturedHandlers } = await mountWithFakeStream();
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "x";
    (document.querySelector(".chat-input .primary") as HTMLButtonElement).click();
    capturedHandlers.at(-1)!.onError?.("boom");
    const err = document.querySelector(".chat-error") as HTMLElement;
    expect(err).not.toBeNull();
    expect(err.textContent).toContain("boom");
    expect(document.querySelectorAll(".chat-loading").length).toBe(0);
    expect((document.querySelector(".chat-input .primary") as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it("Done with no text leaves only the user message (no empty placeholder ever created)", async () => {
    const { capturedHandlers } = await mountWithFakeStream();
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "ping";
    (document.querySelector(".chat-input .primary") as HTMLButtonElement).click();
    // Only the user message exists pre-stream — no empty assistant placeholder.
    expect(document.querySelectorAll(".chat-msg").length).toBe(1);
    capturedHandlers.at(-1)!.onDone?.();
    expect(document.querySelectorAll(".chat-msg").length).toBe(1);
    expect(document.querySelectorAll(".chat-loading").length).toBe(0);
  });

  it("Reset aborts an in-flight stream, clears the transcript and removes the loader", async () => {
    const r = await mountWithFakeStream();
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "thinking";
    (document.querySelector(".chat-input .primary") as HTMLButtonElement).click();
    // User message + loading dots.
    expect(document.querySelectorAll(".chat-msg").length).toBe(1);
    expect(document.querySelectorAll(".chat-loading").length).toBe(1);
    (document.querySelector(".chat-input .secondary") as HTMLButtonElement).click();
    expect(r.abortCalls).toBe(1);
    expect(document.querySelectorAll(".chat-msg").length).toBe(0);
    expect(document.querySelectorAll(".chat-loading").length).toBe(0);
    expect(localStorage.getItem(TURNS_KEY)).toBeNull();
  });
});

// ── prettyToolStart label coverage ────────────────────────────────────────

describe("renderChat — tool-start labels", () => {
  /**
   * Drive `onToolStart` with each known tool name + an unknown one and assert
   * the pill text matches the expected glyph/label combination.
   */
  it("renders a friendly label per tool name", async () => {
    vi.resetModules();
    let handlers!: ChatStreamHandlers;
    vi.doMock("../api", async () => {
      const real = await vi.importActual<typeof import("../api")>("../api");
      return {
        ...real,
        api: {
          ...real.api,
          profile: async () => ({ markdown: "" }),
          chatStream: (_h: unknown, hs: ChatStreamHandlers) => {
            handlers = hs;
            return { abort: () => {} } as unknown as AbortController;
          },
        },
      };
    });
    const { renderChat: rc } = await import("../views/chat");
    document.body.appendChild(await rc());
    await new Promise((r) => setTimeout(r, 0));
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "go";
    (document.querySelector(".chat-input .primary") as HTMLButtonElement).click();
    for (const [i, name] of [
      "patch_profile",
      "add_source",
      "remove_source",
      "read_profile",
      "read_recent_feedback",
      "read_recent_digests",
      "read_recent_curation_runs",
      "list_sources",
      "end_reflection",
      "some_other_tool",
      "", // empty name — exercises the `name || "tool call"` fallback
    ].entries()) {
      handlers.onToolStart?.({ id: `t${i}`, name, input: {} });
    }
    const labels = Array.from(document.querySelectorAll(".chat-tool-card-label")).map(
      (p) => p.textContent ?? "",
    );
    expect(labels).toContain("editing profile.md");
    expect(labels).toContain("adding source");
    expect(labels).toContain("removing source");
    expect(labels).toContain("reading profile");
    expect(labels).toContain("reading recent feedback");
    expect(labels).toContain("reading recent digests");
    expect(labels).toContain("reading curation runs");
    expect(labels).toContain("listing sources");
    expect(labels).toContain("wrapping up");
    expect(labels).toContain("some_other_tool");
    expect(labels).toContain("tool call");
  });
});

// ── markdown renderer + persistItems failure ───────────────────────────────

describe("renderChat — markdown rendering", () => {
  it("renders headings, lists, paragraphs, inline code, and HTML-escapes input", async () => {
    stubProfileFetch(
      "# Title\n\n" +
        "Plain paragraph with `inline` code and <b>bold-not-bold</b>.\n\n" +
        "## Section\n" +
        "- one\n" +
        "- two\n" +
        "\n" +
        "Another paragraph.\n",
    );
    document.body.appendChild(await renderChat());
    await new Promise((r) => setTimeout(r, 5));
    const body = (document.querySelector(".chat-profile-body") as HTMLElement).innerHTML;
    expect(body).toContain("<h2>Title</h2>");
    expect(body).toContain("<h3>Section</h3>");
    expect(body).toContain("<code>inline</code>");
    expect(body).toContain("&lt;b&gt;");
    expect(body).toContain("<ul>");
    expect(body).toContain("<li>one</li>");
  });

  it("emits the empty-profile placeholder when source is blank", async () => {
    stubProfileFetch("");
    document.body.appendChild(await renderChat());
    await new Promise((r) => setTimeout(r, 5));
    expect((document.querySelector(".chat-profile-body") as HTMLElement).innerHTML).toContain(
      "(profile is empty)",
    );
  });

  it("persistItems failures are swallowed silently", async () => {
    stubProfileFetch();
    const el = await renderChat();
    document.body.appendChild(el);

    // Mount succeeded; now make TURNS_KEY writes throw to drive the catch path.
    const origSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (k: string, v: string) {
      if (k === "chat:turns") throw new Error("quota");
      return origSetItem.call(this, k, v);
    };
    try {
      const ta = document.querySelector("textarea") as HTMLTextAreaElement;
      ta.value = "x";
      // Must not throw — persistItems swallows the storage error.
      (document.querySelector(".chat-input .primary") as HTMLButtonElement).click();
    } finally {
      Storage.prototype.setItem = origSetItem;
    }
  });

  it("renderChat mounts even when localStorage.setItem throws", async () => {
    // Regression: Safari private mode (and quota-exceeded states) make
    // setItem throw. The chat view's setCollapsed write must not bubble
    // that out of the initial mount path.
    stubProfileFetch();
    const origSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error("QuotaExceededError");
    };
    try {
      const el = await renderChat();
      document.body.appendChild(el);
      // Layout still rendered; the collapsed default stuck via the
      // in-memory class toggle even though the persisted preference
      // couldn't be written.
      const layout = document.querySelector(".chat-layout") as HTMLElement;
      expect(layout.classList.contains("profile-collapsed")).toBe(true);
    } finally {
      Storage.prototype.setItem = origSetItem;
    }
  });
});
