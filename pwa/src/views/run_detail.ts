import { api, type DigestItem, type RunDetail, type ToolCallEntry } from "../api";
import { prettyToolName } from "../tool-names";
import { renderPageHeader, renderTabBar } from "./_toolbar";

export async function renderRunDetail(params: Record<string, string>): Promise<HTMLElement> {
  const root = document.createElement("div");

  const run_id = params.run_id ?? "";
  const run = await api.runDetail(run_id);

  const title = run
    ? run.started_at != null
      ? formatStarted(run.started_at)
      : formatDate(run.date)
    : "Run not found";

  root.appendChild(renderPageHeader({ title }));
  root.appendChild(makeBack());

  if (!run) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = `No run found for ${run_id}.`;
    root.appendChild(empty);
    root.appendChild(renderTabBar("runs"));
    return root;
  }

  root.appendChild(renderSummaryRow(run));
  root.appendChild(renderTimeline(run));
  root.appendChild(renderTabBar("runs"));

  return root;
}

function makeBack(): HTMLElement {
  const a = document.createElement("a");
  a.href = "#/runs";
  a.textContent = "all runs";
  a.className = "back-link";
  return a;
}

function renderSummaryRow(run: RunDetail): HTMLElement {
  const row = document.createElement("div");
  row.className = "run-summary-row";

  const kindLabel = run.kind === "reflection" ? "Reflection" : "Curation";
  const stats: [string, string][] = [
    ["Kind", kindLabel],
    ["Duration", run.duration_seconds != null ? formatDuration(run.duration_seconds) : "—"],
  ];
  if (run.kind === "curation" && run.item_count != null) {
    stats.push(["Items", String(run.item_count)]);
  }
  stats.push(["Tool calls", run.tool_calls != null ? String(run.tool_calls) : "—"]);
  if (run.kind === "reflection" && run.profile_patches)
    stats.push(["Profile patches", String(run.profile_patches)]);
  if (run.kind === "reflection" && run.sources_changed)
    stats.push(["Sources changed", String(run.sources_changed)]);

  for (const [label, value] of stats) {
    const s = document.createElement("div");
    s.className = "run-summary-stat";
    s.innerHTML = `<strong>${value}</strong> ${label}`;
    row.appendChild(s);
  }
  return row;
}

function renderTriggeringFeedback(event: Record<string, unknown>): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "run-prompt-block";

  const header = document.createElement("div");
  header.className = "run-prompt-header";
  const label = document.createElement("span");
  label.className = "run-prompt-label";
  label.textContent = "Triggering feedback";
  header.appendChild(label);
  wrap.appendChild(header);

  const body = document.createElement("pre");
  body.className = "run-prompt-text";
  body.textContent = JSON.stringify(event, null, 2);
  wrap.appendChild(body);

  return wrap;
}

function renderTimeline(run: RunDetail): HTMLElement {
  const wrap = document.createElement("div");

  if (run.kind === "reflection") {
    if (run.triggering_feedback)
      wrap.appendChild(
        renderTriggeringFeedback(run.triggering_feedback as Record<string, unknown>),
      );
    if (run.exit_reason === "error" && run.error) wrap.appendChild(renderNote("Error", run.error));
    if (run.system_prompt || run.user_prompt)
      wrap.appendChild(
        /* v8 ignore next -- nullish-coalescing fallback for older API payloads; current schema always returns strings */
        renderPromptBlock(run.system_prompt ?? "", run.user_prompt ?? ""),
      );
    for (const entry of run.tool_log) wrap.appendChild(renderEvent(entry));
    if (run.reflection_notes) wrap.appendChild(renderNote("Reflection", run.reflection_notes));
  } else {
    if (run.system_prompt || run.user_prompt)
      wrap.appendChild(
        /* v8 ignore next -- nullish-coalescing fallback for older API payloads; current schema always returns strings */
        renderPromptBlock(run.system_prompt ?? "", run.user_prompt ?? ""),
      );
    for (const entry of run.tool_log) wrap.appendChild(renderEvent(entry));
    if (run.agent_notes) wrap.appendChild(renderNote("Agent", run.agent_notes));
  }

  if (run.items.length) {
    wrap.appendChild(phaseLabel(`Items submitted · ${run.items.length}`));
    for (const item of run.items) wrap.appendChild(renderItem(item));
  }

  return wrap;
}

function renderPromptBlock(systemPrompt: string, userPrompt: string): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "run-prompt-block";

  const header = document.createElement("div");
  header.className = "run-prompt-header";

  const label = document.createElement("span");
  label.className = "run-prompt-label";
  label.textContent = "Prompt";
  header.appendChild(label);

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "run-prompt-toggle";
  toggle.textContent = "Show";
  toggle.setAttribute("aria-expanded", "false");
  header.appendChild(toggle);
  wrap.appendChild(header);

  const body = document.createElement("div");
  body.className = "run-prompt-body collapsed";

  if (systemPrompt) {
    const sysLabel = document.createElement("div");
    sysLabel.className = "run-prompt-section-label";
    sysLabel.textContent = "System";
    body.appendChild(sysLabel);
    const sysText = document.createElement("pre");
    sysText.className = "run-prompt-text";
    sysText.textContent = systemPrompt;
    body.appendChild(sysText);
  }

  if (userPrompt) {
    const userLabel = document.createElement("div");
    userLabel.className = "run-prompt-section-label";
    userLabel.textContent = "User";
    body.appendChild(userLabel);
    const userText = document.createElement("pre");
    userText.className = "run-prompt-text";
    userText.textContent = userPrompt;
    body.appendChild(userText);
  }

  wrap.appendChild(body);

  toggle.onclick = () => {
    const collapsed = body.classList.toggle("collapsed");
    toggle.textContent = collapsed ? "Show" : "Hide";
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  };

  return wrap;
}

function phaseLabel(text: string): HTMLElement {
  const el = document.createElement("div");
  el.className = "run-phase-label";
  el.textContent = text;
  return el;
}

// Badge text + category for each tool name
function badgeInfo(tool: string): { text: string; cls: string } {
  if (tool === "fetch_rss") return { text: "RSS", cls: "run-badge-fetch" };
  if (tool === "fetch_youtube_channel") return { text: "YT", cls: "run-badge-fetch" };
  if (tool === "fetch_youtube_transcript") return { text: "YT▶", cls: "run-badge-fetch" };
  if (tool === "web_fetch" || tool === "WebFetch") return { text: "WEB", cls: "run-badge-fetch" };
  if (tool === "WebSearch") return { text: "SRH", cls: "run-badge-fetch" };
  if (tool.startsWith("read_")) return { text: "READ", cls: "run-badge-read" };
  if (tool === "list_sources") return { text: "LIST", cls: "run-badge-read" };
  if (tool === "submit_digest") return { text: "SUB", cls: "run-badge-action" };
  if (tool === "end_reflection") return { text: "END", cls: "run-badge-action" };
  if (tool === "edit_profile" || tool === "patch_profile")
    return { text: "EDT", cls: "run-badge-mutate" };
  if (tool === "add_source") return { text: "ADD", cls: "run-badge-mutate" };
  if (tool === "remove_source") return { text: "REM", cls: "run-badge-mutate" };
  return { text: tool.slice(0, 3).toUpperCase(), cls: "run-badge-read" };
}

// Condense args into a short string, using hostname for URLs
function condensedArgs(args: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(args)) {
    if (v === null || v === undefined) continue;
    if (typeof v === "string" && (k === "url" || v.startsWith("http"))) {
      try {
        parts.push(`${k}=${new URL(v).hostname}`);
      } catch {
        parts.push(`${k}=${v}`);
      }
    } else if (Array.isArray(v)) {
      parts.push(`${k}=[${v.length}]`);
    } else if (typeof v === "object") {
      parts.push(`${k}={…}`);
    } else {
      const str = String(v);
      parts.push(`${k}=${str.length > 40 ? str.slice(0, 38) + "…" : str}`);
    }
  }
  return parts.join("  ");
}

function renderThinking(text: string): HTMLElement {
  const row = document.createElement("div");
  row.className = "run-event run-think-row";

  const badge = document.createElement("div");
  badge.className = "run-event-badge run-badge-think";
  badge.textContent = "THK";
  row.appendChild(badge);

  const body = document.createElement("div");
  body.className = "run-event-body";

  const preview = document.createElement("div");
  preview.className = "run-think-preview";
  preview.textContent = text.slice(0, 80) + (text.length > 80 ? "…" : "");
  body.appendChild(preview);

  const full = document.createElement("div");
  full.className = "run-think-full collapsed";
  full.textContent = text;
  body.appendChild(full);

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "run-note-toggle-btn";
  toggle.textContent = "Expand thinking";
  toggle.setAttribute("aria-expanded", "false");
  toggle.onclick = () => {
    const collapsed = full.classList.toggle("collapsed");
    preview.hidden = !collapsed;
    toggle.textContent = collapsed ? "Expand thinking" : "Collapse thinking";
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  };
  body.appendChild(toggle);

  row.appendChild(body);
  return row;
}

function renderEvent(entry: ToolCallEntry): HTMLElement {
  const wrap = document.createElement("div");

  if (entry.thinking) {
    wrap.appendChild(renderThinking(entry.thinking));
  }

  const row = document.createElement("div");
  row.className = "run-event";

  const { text, cls } = badgeInfo(entry.tool);
  const badge = document.createElement("div");
  badge.className = `run-event-badge ${cls}`;
  badge.textContent = text;
  row.appendChild(badge);

  const body = document.createElement("div");
  body.className = "run-event-body";

  const toolLine = document.createElement("div");
  toolLine.className = "run-event-tool";
  const toolName = document.createElement("span");
  toolName.textContent = prettyToolName(entry.tool);
  toolLine.appendChild(toolName);

  const argsStr = condensedArgs(entry.args);
  if (argsStr) {
    const argsEl = document.createElement("span");
    argsEl.className = "run-event-args";
    argsEl.textContent = argsStr;
    toolLine.appendChild(argsEl);
  }
  body.appendChild(toolLine);

  if (entry.outcome) {
    const outcome = document.createElement("div");
    outcome.className = "run-event-outcome";
    outcome.textContent = entry.outcome;
    body.appendChild(outcome);
  }

  const detailsEl = renderDetails(entry);
  if (detailsEl) body.appendChild(detailsEl);

  row.appendChild(body);
  wrap.appendChild(row);
  return wrap;
}

function renderDetails(entry: ToolCallEntry): HTMLElement | null {
  const details = entry.details;
  if (!details) return null;
  const sections: HTMLElement[] = [];
  for (const [key, value] of Object.entries(details)) {
    if (value === null || value === undefined) continue;
    if (typeof value === "string" && value === "") continue;
    if (Array.isArray(value) && value.length === 0) continue;
    sections.push(renderDetailSection(entry.tool, key, value));
  }
  if (!sections.length) return null;

  const wrap = document.createElement("div");
  wrap.className = "run-event-details";

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "run-event-details-toggle";
  toggle.setAttribute("aria-expanded", "false");
  toggle.textContent = "Show details";

  const body = document.createElement("div");
  body.className = "run-event-details-body collapsed";
  for (const s of sections) body.appendChild(s);

  toggle.onclick = () => {
    const collapsed = body.classList.toggle("collapsed");
    toggle.textContent = collapsed ? "Show details" : "Hide details";
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  };

  wrap.appendChild(toggle);
  wrap.appendChild(body);
  return wrap;
}

function renderDetailSection(tool: string, key: string, value: unknown): HTMLElement {
  const section = document.createElement("div");
  section.className = "run-event-detail-section";

  const label = document.createElement("div");
  label.className = "run-event-detail-label";
  label.textContent = detailLabel(tool, key);
  section.appendChild(label);

  section.appendChild(renderDetailValue(value));
  return section;
}

function detailLabel(_tool: string, key: string): string {
  const map: Record<string, string> = {
    profile: "profile.md",
    profile_snapshot: "profile.md (snapshot)",
    entries: "Entries",
    items: "Items",
    sources: "Sources",
    events: "Feedback events",
    prior_events: "Prior feedback events",
    triggering_event: "Triggering event",
    text: "Text",
    diff: "Diff",
    before: "Before",
    after: "After",
    error: "Error",
    agent_notes: "Agent notes",
    notes: "Notes",
    run_ids: "Run IDs",
    matched_run_id: "Matched run",
    title: "Title",
    url: "URL",
    kind: "Kind",
    value: "Value",
    name: "Name",
    tags: "Tags",
    rejected: "Rejected",
  };
  return map[key] ?? key;
}

function renderDetailValue(value: unknown): HTMLElement {
  if (typeof value === "string") {
    const pre = document.createElement("pre");
    pre.className = "run-event-detail-pre";
    pre.textContent = value;
    return pre;
  }
  if (Array.isArray(value)) {
    if (value.every((v) => v && typeof v === "object" && !Array.isArray(v))) {
      return renderObjectList(value as Record<string, unknown>[]);
    }
    const pre = document.createElement("pre");
    pre.className = "run-event-detail-pre";
    pre.textContent = JSON.stringify(value, null, 2);
    return pre;
  }
  if (value && typeof value === "object") {
    const pre = document.createElement("pre");
    pre.className = "run-event-detail-pre";
    pre.textContent = JSON.stringify(value, null, 2);
    return pre;
  }
  const span = document.createElement("div");
  span.className = "run-event-detail-pre";
  span.textContent = String(value);
  return span;
}

function renderObjectList(rows: Record<string, unknown>[]): HTMLElement {
  const list = document.createElement("ul");
  list.className = "run-event-detail-list";
  for (const row of rows) {
    const li = document.createElement("li");
    const title = pickString(row, "title") ?? pickString(row, "name") ?? pickString(row, "value");
    const url = pickString(row, "url") ?? pickString(row, "link");
    const sub =
      pickString(row, "source") ??
      pickString(row, "kind") ??
      pickString(row, "type") ??
      pickString(row, "published_at") ??
      pickString(row, "ts");

    const safeUrl = url && /^https?:/i.test(url) ? url : undefined;
    const label = title ?? sub ?? JSON.stringify(row);
    const row_title = document.createElement("div");
    row_title.className = "run-event-detail-row-title";
    if (safeUrl) {
      const a = document.createElement("a");
      a.href = safeUrl;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = label;
      row_title.appendChild(a);
    } else {
      row_title.textContent = label;
    }
    li.appendChild(row_title);
    if (title && sub) {
      const s = document.createElement("div");
      s.className = "run-event-detail-row-sub";
      s.textContent = sub;
      li.appendChild(s);
    }
    list.appendChild(li);
  }
  return list;
}

function pickString(obj: Record<string, unknown>, key: string): string | undefined {
  const v = obj[key];
  return typeof v === "string" && v ? v : undefined;
}

function renderNote(label: string, text: string): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "run-note";

  const lbl = document.createElement("div");
  lbl.className = "run-note-label";
  lbl.textContent = label;
  wrap.appendChild(lbl);

  const body = document.createElement("div");
  body.className = "run-note-body";
  body.textContent = text;

  const lineCount = text.split("\n").length;
  const approxLines = Math.ceil(text.length / 60) + lineCount;
  const needsClamp = approxLines > 5;

  if (needsClamp) {
    body.classList.add("clamped");
    wrap.appendChild(body);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "run-note-toggle-btn";
    toggle.textContent = "Show more";
    toggle.setAttribute("aria-expanded", "false");
    toggle.onclick = () => {
      const clamped = body.classList.toggle("clamped");
      toggle.textContent = clamped ? "Show more" : "Show less";
      toggle.setAttribute("aria-expanded", clamped ? "false" : "true");
    };
    wrap.appendChild(toggle);
  } else {
    wrap.appendChild(body);
  }

  return wrap;
}

function renderItem(item: DigestItem): HTMLElement {
  const row = document.createElement("div");
  row.className = "run-item-row";
  const a = document.createElement("a");
  a.href = item.url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = item.title;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `${item.source} · ${item.type}`;
  row.appendChild(a);
  row.appendChild(meta);
  return row;
}

function formatStarted(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}
