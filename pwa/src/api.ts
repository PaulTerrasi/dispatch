// Tiny typed fetch wrapper. Response shapes are aliased from `api-types.ts`,
// which is regenerated from the FastAPI OpenAPI schema by `make types`. Don't
// duplicate model fields here — change Python pydantic, run `make types`, done.

import type { components } from "./api-types";

type Schemas = components["schemas"];

// Request-side literal unions. The server accepts a wider `string` per the
// OpenAPI schema, but the PWA only sends these values, so narrow here for
// caller-side type safety.
export type ItemType = "article" | "video";
export type ThumbValue = "up" | "down" | "none";

// Response-side aliases from generated types. Keep the local names (DigestItem,
// RunSummary, etc.) so call sites in views don't have to change shape.
export type DigestItem = Schemas["DigestItem"];
export type FeedItem = Schemas["FeedItem"];
export type Digest = Schemas["Digest"];
export type DigestSummary = Schemas["DigestSummary"];
export type SearchHit = Schemas["SearchHit"];
export type ToolCallEntry = Schemas["ToolCallEntry"];
// API names this RunSummaryOut; PWA has always called it RunSummary.
export type RunSummary = Schemas["RunSummaryOut"];
export type RunDetail = Schemas["RunDetail"];

// VITE_AUTH_TOKEN is set at build time when deploying to AWS.
// Undefined in local dev (no auth middleware in that case).
const AUTH_TOKEN = import.meta.env.VITE_AUTH_TOKEN as string | undefined;

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (AUTH_TOKEN) h["Authorization"] = `Bearer ${AUTH_TOKEN}`;
  return h;
}

async function jsonOr<T>(res: Response, fallback: T): Promise<T> {
  if (res.status === 404) return fallback;
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

function get(url: string) {
  return fetch(url, { headers: authHeaders() });
}

function post(url: string, body: unknown) {
  return fetch(url, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
}

export const api = {
  today: () => get("/api/digest/today").then((r) => jsonOr<Digest | null>(r, null)),
  feed: () => get("/api/feed").then((r) => jsonOr<FeedItem[]>(r, [])),
  byDate: (d: string, feedbackOnly = false) =>
    get(`/api/digest/${d}${feedbackOnly ? "?feedback_only=true" : ""}`).then((r) =>
      jsonOr<Digest | null>(r, null),
    ),
  list: () => get("/api/digests").then((r) => jsonOr<DigestSummary[]>(r, [])),
  search: (q: string) =>
    get(`/api/search?q=${encodeURIComponent(q)}`).then((r) => jsonOr<SearchHit[]>(r, [])),
  thumb: (item_id: string, value: ThumbValue, notes?: string) =>
    post("/api/feedback", { item_id, value, ...(notes ? { notes } : {}) }).then((r) =>
      jsonOr<{ status: string }>(r, { status: "error" }),
    ),
  profile: () => get("/api/profile").then((r) => jsonOr<{ markdown: string }>(r, { markdown: "" })),
  chatStream: (
    history: { role: "user" | "assistant"; text: string }[],
    handlers: ChatStreamHandlers,
  ) => chatStream(history, handlers),
  runs: () => get("/api/runs").then((r) => jsonOr<RunSummary[]>(r, [])),
  runDetail: (run_id: string) =>
    get(`/api/runs/${run_id}`).then((r) => jsonOr<RunDetail | null>(r, null)),
};

// ── Chat-tab streaming ───────────────────────────────────────────────────────
// SSE consumed via fetch + ReadableStream because EventSource cannot send the
// Authorization header. Frame format: `event: <name>\ndata: <json>\n\n`.

export interface ChatStreamHandlers {
  onText?: (delta: string) => void;
  onReasoning?: (delta: string) => void;
  onToolStart?: (tool: { id: string; name: string; input: unknown }) => void;
  onToolEnd?: (tool: { tool_use_id: string; ok: boolean; output?: string }) => void;
  onProfileChanged?: (by: string) => void;
  onDone?: () => void;
  onError?: (message: string) => void;
}

function chatStream(
  history: { role: "user" | "assistant"; text: string }[],
  handlers: ChatStreamHandlers,
): AbortController {
  const ctl = new AbortController();
  void runChatStream(history, handlers, ctl.signal).catch((e: unknown) => {
    if ((e as { name?: string })?.name === "AbortError") return;
    handlers.onError?.((e as Error)?.message ?? "stream failed");
  });
  return ctl;
}

const STALL_TIMEOUT_MS = 20_000;

async function runChatStream(
  history: { role: "user" | "assistant"; text: string }[],
  handlers: ChatStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: authHeaders({
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    }),
    body: JSON.stringify({ history }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`chat stream failed: ${res.status} ${res.statusText}`);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let stallTimer: ReturnType<typeof setTimeout> | null = null;
  let stalled = false;
  const armStall = (): void => {
    if (stallTimer) clearTimeout(stallTimer);
    stallTimer = setTimeout(() => {
      stalled = true;
      void reader.cancel();
    }, STALL_TIMEOUT_MS);
  };
  try {
    armStall();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      armStall();
      buffer += value;
      let sepIdx: number;
      while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);
        dispatchSseFrame(frame, handlers);
      }
    }
    if (stalled) {
      throw new Error("chat stream stalled: no server activity for 20s");
    }
  } finally {
    if (stallTimer) clearTimeout(stallTimer);
    reader.releaseLock();
  }
}

function dispatchSseFrame(frame: string, handlers: ChatStreamHandlers): void {
  let event = "message";
  let dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  if (!dataLines.length) return;
  let data: unknown;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch {
    return; // malformed; skip silently
  }
  switch (event) {
    case "text":
      handlers.onText?.((data as { delta: string }).delta);
      break;
    case "reasoning":
      handlers.onReasoning?.((data as { delta: string }).delta);
      break;
    case "tool_start":
      handlers.onToolStart?.(data as { id: string; name: string; input: unknown });
      break;
    case "tool_end":
      handlers.onToolEnd?.(data as { tool_use_id: string; ok: boolean; output?: string });
      break;
    case "profile_changed":
      handlers.onProfileChanged?.((data as { by: string }).by);
      break;
    case "heartbeat":
      break;
    case "done":
      handlers.onDone?.();
      break;
    case "error":
      handlers.onError?.((data as { message: string }).message);
      break;
  }
}
