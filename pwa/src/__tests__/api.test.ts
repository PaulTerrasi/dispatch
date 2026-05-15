import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { api } from "../api";

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubResponses(routes: Record<string, { status?: number; body?: unknown }>) {
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
    async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      for (const [path, spec] of Object.entries(routes)) {
        if (url.startsWith(path)) {
          return new Response(JSON.stringify(spec.body ?? null), {
            status: spec.status ?? 200,
            statusText: spec.status && spec.status >= 400 ? "ERR" : "OK",
          });
        }
      }
      throw new Error(`no stub for ${url}`);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("api typed wrappers", () => {
  it("today returns the digest payload", async () => {
    stubResponses({ "/api/digest/today": { body: { date: "2026-05-14", items: [] } } });
    const got = await api.today();
    expect(got?.date).toBe("2026-05-14");
  });

  it("today returns null on 404", async () => {
    stubResponses({ "/api/digest/today": { status: 404 } });
    expect(await api.today()).toBeNull();
  });

  it("today throws on non-404 errors", async () => {
    stubResponses({ "/api/digest/today": { status: 500 } });
    await expect(api.today()).rejects.toThrow("500");
  });

  it("feed returns [] on 404 and unwraps array on 200", async () => {
    stubResponses({ "/api/feed": { status: 404 } });
    expect(await api.feed()).toEqual([]);

    stubResponses({ "/api/feed": { body: [{ id: "x" }] } });
    expect((await api.feed())[0].id).toBe("x");
  });

  it("byDate composes the URL and honors feedback_only flag", async () => {
    const f = stubResponses({ "/api/digest/2026-05-14": { body: { date: "2026-05-14" } } });
    await api.byDate("2026-05-14");
    expect(f.mock.calls[0][0]).toBe("/api/digest/2026-05-14");
    await api.byDate("2026-05-14", true);
    expect(f.mock.calls[1][0]).toBe("/api/digest/2026-05-14?feedback_only=true");
  });

  it("list returns archive summaries", async () => {
    stubResponses({ "/api/digests": { body: [{ date: "2026-05-14", item_count: 2 }] } });
    const out = await api.list();
    expect(out[0].item_count).toBe(2);
  });

  it("search URL-encodes the query", async () => {
    const f = stubResponses({ "/api/search": { body: [] } });
    await api.search("hello world");
    expect(f.mock.calls[0][0]).toContain("q=hello%20world");
  });

  it("thumb omits notes when absent and includes them when present", async () => {
    const f = stubResponses({ "/api/feedback": { body: { status: "ok" } } });
    await api.thumb("abc", "up");
    let body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ item_id: "abc", value: "up" });

    await api.thumb("abc", "down", "more woodworking");
    body = JSON.parse((f.mock.calls[1][1] as RequestInit).body as string);
    expect(body).toEqual({ item_id: "abc", value: "down", notes: "more woodworking" });
  });

  it("thumb falls back to a synthetic error payload on 404", async () => {
    stubResponses({ "/api/feedback": { status: 404 } });
    expect(await api.thumb("missing", "up")).toEqual({ status: "error" });
  });

  it("profile returns markdown shape; defaults to empty string on 404", async () => {
    stubResponses({ "/api/profile": { body: { markdown: "# hi" } } });
    expect((await api.profile()).markdown).toBe("# hi");
    stubResponses({ "/api/profile": { status: 404 } });
    expect((await api.profile()).markdown).toBe("");
  });

  it("runs / runDetail wrappers", async () => {
    stubResponses({
      "/api/runs/r1": { body: { run_id: "r1", date: "2026-05-14" } },
      "/api/runs": { body: [{ run_id: "r1" }] },
    });
    expect((await api.runs())[0].run_id).toBe("r1");
    const detail = await api.runDetail("r1");
    expect(detail?.run_id).toBe("r1");

    stubResponses({ "/api/runs/missing": { status: 404 } });
    expect(await api.runDetail("missing")).toBeNull();
  });
});

// ── chatStream SSE parsing ─────────────────────────────────────────────────

function makeSseResponse(frames: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      for (const f of frames) controller.enqueue(enc.encode(f));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

describe("api.chatStream", () => {
  it("dispatches text/tool_start/tool_end/profile_changed/done events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        makeSseResponse([
          'event: text\ndata: {"delta":"Hi"}\n\n',
          'event: tool_start\ndata: {"id":"t1","name":"add_source","input":{}}\n\n',
          'event: tool_end\ndata: {"tool_use_id":"t1","ok":true}\n\n',
          'event: profile_changed\ndata: {"by":"add_source"}\n\n',
          "event: heartbeat\ndata: {}\n\n",
          "event: done\ndata: {}\n\n",
        ]),
      ),
    );

    const events: string[] = [];
    const text: string[] = [];
    const ctl = api.chatStream([{ role: "user", text: "hi" }], {
      onText: (d) => text.push(d),
      onToolStart: (t) => events.push(`start:${t.name}`),
      onToolEnd: (t) => events.push(`end:${t.ok}`),
      onProfileChanged: (by) => events.push(`changed:${by}`),
      onDone: () => events.push("done"),
      onError: () => events.push("error"),
    });
    expect(ctl).toBeInstanceOf(AbortController);
    // wait for the stream to drain
    await new Promise((r) => setTimeout(r, 10));
    expect(text).toEqual(["Hi"]);
    expect(events).toEqual(["start:add_source", "end:true", "changed:add_source", "done"]);
  });

  it("invokes onError when the server returns a non-OK response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nope", { status: 500, statusText: "Server" })),
    );
    const errs: string[] = [];
    api.chatStream([{ role: "user", text: "x" }], {
      onError: (m) => errs.push(m),
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(errs.length).toBe(1);
    expect(errs[0]).toContain("500");
  });

  it("forwards error frames from the server to onError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => makeSseResponse(['event: error\ndata: {"message":"agent timed out"}\n\n'])),
    );
    const errs: string[] = [];
    api.chatStream([{ role: "user", text: "x" }], { onError: (m) => errs.push(m) });
    await new Promise((r) => setTimeout(r, 10));
    expect(errs).toEqual(["agent timed out"]);
  });

  it("silently drops malformed SSE frames (no data: or invalid JSON)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        makeSseResponse([
          "event: text\n\n", // no data line
          "event: text\ndata: {not-json}\n\n", // invalid JSON
          'event: text\ndata: {"delta":"ok"}\n\n',
          "event: done\ndata: {}\n\n",
        ]),
      ),
    );
    const text: string[] = [];
    let doneCount = 0;
    api.chatStream([{ role: "user", text: "x" }], {
      onText: (d) => text.push(d),
      onDone: () => doneCount++,
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(text).toEqual(["ok"]);
    expect(doneCount).toBe(1);
  });

  it("ignores unknown event types without crashing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        makeSseResponse(['event: mystery\ndata: {"x":1}\n\n', "event: done\ndata: {}\n\n"]),
      ),
    );
    let done = false;
    api.chatStream([{ role: "user", text: "x" }], { onDone: () => (done = true) });
    await new Promise((r) => setTimeout(r, 10));
    expect(done).toBe(true);
  });

  it("non-AbortError fetch failures are reported via onError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("offline");
      }),
    );
    const errs: string[] = [];
    api.chatStream([{ role: "user", text: "x" }], { onError: (m) => errs.push(m) });
    await new Promise((r) => setTimeout(r, 10));
    expect(errs).toEqual(["offline"]);
  });

  it("non-Error throwables surface a generic 'stream failed' message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw "weird-string";
      }),
    );
    const errs: string[] = [];
    api.chatStream([{ role: "user", text: "x" }], { onError: (m) => errs.push(m) });
    await new Promise((r) => setTimeout(r, 10));
    expect(errs).toEqual(["stream failed"]);
  });

  it("survives an aborted stream without invoking onError", async () => {
    // A fetch that rejects with an AbortError; chatStream's caller-facing
    // promise should swallow it (the user just cancelled the request).
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input, init?: RequestInit) => {
        await new Promise((r) => setTimeout(r, 5));
        if (init?.signal?.aborted) {
          const err = new Error("aborted");
          (err as Error & { name: string }).name = "AbortError";
          throw err;
        }
        return makeSseResponse([]);
      }),
    );
    const errs: string[] = [];
    const ctl = api.chatStream([{ role: "user", text: "x" }], {
      onError: (m) => errs.push(m),
    });
    ctl.abort();
    await new Promise((r) => setTimeout(r, 20));
    expect(errs).toEqual([]);
  });

  it("times out a stalled stream and reports the stall", async () => {
    // A stream that opens but never produces any data; with a tightened
    // STALL_TIMEOUT we expect onError to fire. Since STALL_TIMEOUT_MS is
    // an internal constant, we patch performance by using fake timers.
    vi.useFakeTimers();
    const pullResolveBox: { fn: (() => void) | null } = { fn: null };
    const body = new ReadableStream<Uint8Array>({
      pull(_controller) {
        return new Promise<void>((res) => {
          pullResolveBox.fn = res;
        });
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(body, { status: 200 })),
    );
    const errs: string[] = [];
    api.chatStream([{ role: "user", text: "x" }], { onError: (m) => errs.push(m) });
    // Drive past the stall window (20s).
    await vi.advanceTimersByTimeAsync(20_100);
    // Force the stream to flush so the read() resolves with done=true.
    pullResolveBox.fn?.();
    await vi.runAllTimersAsync();
    vi.useRealTimers();
    await new Promise((r) => setTimeout(r, 5));
    expect(errs.some((m) => m.toLowerCase().includes("stall"))).toBe(true);
  });
});

describe("api auth headers", () => {
  it("attaches Bearer token when VITE_AUTH_TOKEN is set", async () => {
    // Vite injects env at build; we can poke import.meta.env at runtime under vitest.
    const env = import.meta.env as Record<string, unknown>;
    const prev = env.VITE_AUTH_TOKEN;
    env.VITE_AUTH_TOKEN = "secret-token";

    try {
      // Re-import the module so the new env is picked up.
      vi.resetModules();
      const reloaded = await import("../api");
      const f = stubResponses({ "/api/digest/today": { body: { date: "x", items: [] } } });
      await reloaded.api.today();
      const init = f.mock.calls[0][1] as RequestInit;
      const headers = init.headers as Record<string, string>;
      expect(headers["Authorization"]).toBe("Bearer secret-token");
    } finally {
      // Cleanup is unconditional — a failing assertion above otherwise
      // leaks the mutated env into subsequent tests.
      env.VITE_AUTH_TOKEN = prev;
      vi.resetModules();
    }
  });
});
