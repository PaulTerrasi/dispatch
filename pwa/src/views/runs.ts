import { api, type RunSummary } from "../api";
import { renderPageHeader, renderTabBar } from "./_toolbar";
import { dayKeyOf, formatDayLong, formatInstantTime } from "../time";

export async function renderRuns(): Promise<HTMLElement> {
  const root = document.createElement("div");
  root.appendChild(
    renderPageHeader({
      title: "Runs",
      subtitle: "Every curation and reflection pass.",
    }),
  );

  const runs = await api.runs();

  if (!runs.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No runs recorded yet.";
    root.appendChild(empty);
    root.appendChild(renderTabBar("runs"));
    return root;
  }

  // Group by calendar date
  const groups = new Map<string, RunSummary[]>();
  for (const run of runs) {
    const key = run.started_at ? dayKeyOf(run.started_at) : run.date;
    const bucket = groups.get(key);
    if (bucket) {
      bucket.push(run);
    } else {
      groups.set(key, [run]);
    }
  }

  for (const [dateKey, group] of groups) {
    const header = document.createElement("div");
    header.className = "run-group-header";
    header.textContent = formatDayLong(dateKey, { year: true });
    root.appendChild(header);

    for (const run of group) {
      root.appendChild(renderRunCard(run));
    }
  }

  root.appendChild(renderTabBar("runs"));
  return root;
}

function renderRunCard(run: RunSummary): HTMLElement {
  return run.kind === "reflection" ? renderReflectionCard(run) : renderCurationCard(run);
}

function renderCurationCard(run: RunSummary): HTMLElement {
  const card = baseCard(run, "curation");

  const body = document.createElement("div");
  body.className = "run-card-body";

  const labelRow = document.createElement("div");
  labelRow.className = "run-card-kind";
  labelRow.textContent = "Curation";
  body.appendChild(labelRow);

  const dur = document.createElement("div");
  dur.className = "run-card-duration";
  dur.textContent = run.duration_seconds != null ? formatDuration(run.duration_seconds) : "—";
  body.appendChild(dur);

  const chips = document.createElement("div");
  chips.className = "run-card-chips";
  if (run.item_count)
    chips.appendChild(chip(`${run.item_count} item${run.item_count !== 1 ? "s" : ""}`));
  if (run.tool_calls != null) chips.appendChild(chip(`${run.tool_calls} tools`));

  body.appendChild(chips);
  card.appendChild(body);
  appendChevron(card);
  return card;
}

function renderReflectionCard(run: RunSummary): HTMLElement {
  const card = baseCard(run, "reflection");

  const body = document.createElement("div");
  body.className = "run-card-body";

  const labelRow = document.createElement("div");
  labelRow.className = "run-card-kind";
  labelRow.textContent = "Reflection";
  body.appendChild(labelRow);

  const trigger = document.createElement("div");
  trigger.className = "run-card-trigger";
  trigger.textContent = formatTrigger(run.triggering_feedback);
  body.appendChild(trigger);

  const chips = document.createElement("div");
  chips.className = "run-card-chips";
  if (run.duration_seconds != null) chips.appendChild(chip(formatDuration(run.duration_seconds)));
  if (run.exit_reason === "error") {
    const errText = run.error ? truncate(run.error, 80) : "failed";
    chips.appendChild(errorChip(errText));
  } else {
    if (run.profile_patches)
      chips.appendChild(
        chip(`${run.profile_patches} patch${run.profile_patches !== 1 ? "es" : ""}`),
      );
    if (run.sources_changed)
      chips.appendChild(
        chip(`${run.sources_changed} source${run.sources_changed !== 1 ? "s" : ""} changed`),
      );
    if (!run.profile_patches && !run.sources_changed) chips.appendChild(chip("no changes"));
  }
  body.appendChild(chips);

  card.appendChild(body);
  appendChevron(card);
  return card;
}

function baseCard(run: RunSummary, kind: "curation" | "reflection"): HTMLElement {
  const card = document.createElement("div");
  card.className = `run-card run-card--${kind}`;
  card.setAttribute("role", "button");
  card.setAttribute("tabindex", "0");
  const navigate = () => {
    if (run.run_id) location.hash = `/run/${run.run_id}`;
  };
  card.onclick = navigate;
  card.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") navigate();
  };

  const timeEl = document.createElement("div");
  timeEl.className = "run-card-time";
  timeEl.textContent = run.started_at ? formatInstantTime(run.started_at) : "—";
  card.appendChild(timeEl);
  return card;
}

function appendChevron(card: HTMLElement): void {
  const chevron = document.createElement("div");
  chevron.className = "run-card-chevron";
  chevron.textContent = "›";
  card.appendChild(chevron);
}

function formatTrigger(event: Record<string, unknown> | null | undefined): string {
  if (!event) return "feedback event";
  const kind = typeof event.kind === "string" ? event.kind : "";
  if (kind === "thumb") {
    const value = typeof event.value === "string" ? event.value : "?";
    const itemId = typeof event.item_id === "string" ? event.item_id : "?";
    return `thumb ${value} on ${itemId}`;
  }
  if (kind === "chat") {
    const text = typeof event.text === "string" ? event.text : "";
    const trimmed = text.length > 80 ? text.slice(0, 80) + "…" : text;
    return `chat: "${trimmed}"`;
  }
  return kind || "feedback event";
}

function chip(text: string): HTMLElement {
  const el = document.createElement("span");
  el.className = "run-chip";
  el.textContent = text;
  return el;
}

function errorChip(text: string): HTMLElement {
  const el = document.createElement("span");
  el.className = "run-chip run-chip--error";
  el.textContent = text;
  return el;
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + "…" : s;
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}
