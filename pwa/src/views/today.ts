import { api, type Digest, type DigestItem, type FeedItem, type ThumbValue } from "../api";
import { renderPageHeader, renderTabBar, formatTodayLong } from "./_toolbar";

export async function renderToday(): Promise<HTMLElement> {
  const root = document.createElement("div");

  const items = await api.feed();
  root.appendChild(
    renderPageHeader({
      title: formatTodayLong(),
      subtitle: subtitleFor(items.length),
    }),
  );
  const subtitleEl = root.querySelector<HTMLElement>(".page-subtitle");
  let feedSection: HTMLElement;
  const onItemRemoved = () => {
    const remaining = feedSection.querySelectorAll(".item").length;
    if (subtitleEl) subtitleEl.textContent = subtitleFor(remaining);
  };
  feedSection = renderFeed(items, onItemRemoved);
  root.appendChild(feedSection);
  root.appendChild(renderTabBar("today"));
  return root;
}

function subtitleFor(count: number): string {
  return count === 0
    ? "Inbox zero · nothing new"
    : `${count} new item${count === 1 ? "" : "s"} to review`;
}

function renderFeed(items: FeedItem[], onItemRemoved?: () => void): HTMLElement {
  const wrap = document.createElement("section");

  if (!items.length) {
    wrap.appendChild(emptyState("Inbox zero — nothing new to react to."));
    return wrap;
  }

  // Sort oldest first; group by day (date-line) then run hour (run-line sub-header).
  const sorted = [...items].sort((a, b) => {
    const aKey = a.run_started_at ?? a.digest_date + "T00:00:00Z";
    const bKey = b.run_started_at ?? b.digest_date + "T00:00:00Z";
    return aKey.localeCompare(bKey);
  });

  let currentDate = "";
  let currentRun = "";
  for (const item of sorted) {
    const dateKey = localDateKey(item);
    const runKey = item.run_started_at
      ? `${dateKey}|${new Date(item.run_started_at).getHours()}`
      : dateKey;

    if (dateKey !== currentDate) {
      currentDate = dateKey;
      currentRun = "";
      const dateLine = document.createElement("div");
      dateLine.className = "date-line";
      dateLine.textContent = formatDate(currentDate);
      dateLine.dataset.dateKey = currentDate;
      wrap.appendChild(dateLine);
    }
    if (runKey !== currentRun) {
      currentRun = runKey;
      if (item.run_started_at) {
        const runLine = document.createElement("div");
        runLine.className = "run-line";
        runLine.textContent = new Date(item.run_started_at).toLocaleTimeString(undefined, {
          hour: "numeric",
        });
        runLine.dataset.dateKey = currentDate;
        runLine.dataset.runKey = currentRun;
        wrap.appendChild(runLine);
      }
    }
    const card = renderItem(item, onItemRemoved);
    card.dataset.dateKey = dateKey;
    if (item.run_started_at) card.dataset.runKey = runKey;
    wrap.appendChild(card);
  }

  return wrap;
}

export function renderDigest(digest: Digest): HTMLElement {
  const wrap = document.createElement("section");

  const dateLine = document.createElement("div");
  dateLine.className = "date-line";
  dateLine.textContent = formatDate(digest.date);
  wrap.appendChild(dateLine);

  if (!digest.items.length) {
    const h = document.createElement("h2");
    h.textContent = "Quiet morning";
    wrap.appendChild(h);
    const p = document.createElement("p");
    p.className = "note";
    p.textContent = "Nothing met the bar today.";
    wrap.appendChild(p);
  }

  for (const item of digest.items) {
    wrap.appendChild(renderItem(item));
  }

  return wrap;
}

function renderItem(item: DigestItem, onRemoved?: () => void): HTMLElement {
  const card = document.createElement("article");
  card.className = "item";
  if (item.feedback) card.classList.add("is-reacted");
  card.dataset.itemId = item.id;

  const titleLink = document.createElement("a");
  titleLink.href = item.url;
  titleLink.target = "_blank";
  titleLink.rel = "noopener noreferrer";
  const h = document.createElement("h2");
  h.textContent = item.title;
  titleLink.appendChild(h);
  card.appendChild(titleLink);

  const meta = document.createElement("div");
  meta.className = "meta";
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = item.type;
  meta.appendChild(badge);
  const src = document.createElement("span");
  src.className = "source";
  src.textContent = item.source || hostnameOf(item.url);
  meta.appendChild(src);
  if (item.type === "video" && item.duration_min) {
    const dur = document.createElement("span");
    dur.textContent = `· ${item.duration_min} min`;
    meta.appendChild(dur);
  }
  card.appendChild(meta);

  if (item.summary) {
    const p = document.createElement("p");
    p.textContent = item.summary;
    card.appendChild(p);
  }

  card.appendChild(renderThumbs(item, card, onRemoved));
  return card;
}

function renderThumbs(item: DigestItem, card: HTMLElement, onRemoved?: () => void): HTMLElement {
  const actions = document.createElement("div");
  actions.className = "actions";

  // Note field is hidden until the user explicitly asks for it — frees ~50px
  // of vertical space per item. They tap "add note" to reveal.
  const notesEl = document.createElement("textarea");
  notesEl.className = "feedback-notes hidden";
  notesEl.placeholder = "Add a note… (optional)";
  notesEl.rows = 2;

  const thumbs = document.createElement("div");
  thumbs.className = "thumbs";

  const up = document.createElement("button");
  up.className = "thumb up" + (item.feedback === "up" ? " active" : "");
  up.innerHTML = `<span class="glyph" aria-hidden="true">+</span>more like this`;
  up.setAttribute("aria-label", "More like this");

  const down = document.createElement("button");
  down.className = "thumb down" + (item.feedback === "down" ? " active" : "");
  down.innerHTML = `<span class="glyph" aria-hidden="true">−</span>skip`;
  down.setAttribute("aria-label", "Skip");

  const noteToggle = document.createElement("button");
  noteToggle.type = "button";
  noteToggle.className = "thumb note-toggle-btn";
  noteToggle.innerHTML = `<span class="glyph" aria-hidden="true">✎</span>`;
  noteToggle.setAttribute("aria-label", "Add a note");
  noteToggle.onclick = () => {
    notesEl.classList.toggle("hidden");
    if (!notesEl.classList.contains("hidden")) notesEl.focus();
  };

  const setState = (current: string | null | undefined) => {
    up.classList.toggle("active", current === "up");
    down.classList.toggle("active", current === "down");
  };

  const fadeOutAndRemove = () => {
    card.style.transition = "opacity 200ms ease";
    card.style.opacity = "0";
    card.style.pointerEvents = "none";
    setTimeout(() => {
      const parent = card.parentElement;
      const dateKey = card.dataset.dateKey;
      const runKey = card.dataset.runKey;
      card.remove();
      if (parent) {
        if (runKey && !parent.querySelector(`.item[data-run-key="${cssEscape(runKey)}"]`)) {
          parent.querySelector(`.run-line[data-run-key="${cssEscape(runKey)}"]`)?.remove();
        }
        if (dateKey && !parent.querySelector(`.item[data-date-key="${cssEscape(dateKey)}"]`)) {
          parent.querySelector(`.date-line[data-date-key="${cssEscape(dateKey)}"]`)?.remove();
          parent
            .querySelectorAll(`.run-line[data-date-key="${cssEscape(dateKey)}"]`)
            .forEach((el) => el.remove());
        }
      }
      onRemoved?.();
    }, 200);
  };

  up.onclick = async () => {
    const prev = item.feedback;
    const next: ThumbValue = prev === "up" ? "none" : "up";
    const notes = notesEl.value.trim() || undefined;
    item.feedback = next === "none" ? null : "up";
    setState(item.feedback);
    card.classList.toggle("is-reacted", !!item.feedback);
    await api.thumb(item.id, next, notes);
    if (prev === null && next !== "none") {
      fadeOutAndRemove();
    } else {
      notesEl.value = "";
    }
  };
  down.onclick = async () => {
    const prev = item.feedback;
    const next: ThumbValue = prev === "down" ? "none" : "down";
    const notes = notesEl.value.trim() || undefined;
    item.feedback = next === "none" ? null : "down";
    setState(item.feedback);
    card.classList.toggle("is-reacted", !!item.feedback);
    await api.thumb(item.id, next, notes);
    if (prev === null && next !== "none") {
      fadeOutAndRemove();
    } else {
      notesEl.value = "";
    }
  };

  thumbs.appendChild(up);
  thumbs.appendChild(down);
  thumbs.appendChild(noteToggle);
  actions.appendChild(thumbs);
  actions.appendChild(notesEl);
  return actions;
}

function emptyState(text: string): HTMLElement {
  const d = document.createElement("div");
  d.className = "empty";
  d.textContent = text;
  return d;
}

function localDateKey(item: FeedItem): string {
  const instant = item.run_started_at
    ? new Date(item.run_started_at)
    : new Date(item.digest_date + "T00:00:00Z");
  const y = instant.getFullYear();
  const m = String(instant.getMonth() + 1).padStart(2, "0");
  const d = String(instant.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

function cssEscape(value: string): string {
  return typeof CSS !== "undefined" && CSS.escape
    ? CSS.escape(value)
    : value.replace(/["\\]/g, "\\$&");
}

function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}
