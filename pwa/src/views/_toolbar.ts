export type TabKey = "today" | "archive" | "chat" | "runs";

/**
 * Editorial page header — small-caps wordmark on top, a serif title underneath,
 * and a single coral→gold "horizon" rule. Used at the top of every primary view
 * instead of a generic title bar.
 */
export function renderPageHeader(opts: {
  title: string;
  subtitle?: string;
  showHorizon?: boolean;
}): HTMLElement {
  const header = document.createElement("header");
  header.className = "page-header";

  const wordmark = document.createElement("div");
  wordmark.className = "page-wordmark";
  wordmark.textContent = "dispatch";
  header.appendChild(wordmark);

  const title = document.createElement("h1");
  title.className = "page-title";
  title.textContent = opts.title;
  header.appendChild(title);

  if (opts.subtitle) {
    const sub = document.createElement("div");
    sub.className = "page-subtitle";
    sub.textContent = opts.subtitle;
    header.appendChild(sub);
  }

  if (opts.showHorizon !== false) {
    const rule = document.createElement("hr");
    rule.className = "horizon-rule";
    header.appendChild(rule);
  }

  return header;
}

/**
 * Fixed bottom tab bar with inline SVG icons. Hidden on the chat page via CSS
 * (chat owns the full viewport). Renders after `main` so it sits above
 * scrolling content.
 */
export function renderTabBar(active: TabKey): HTMLElement {
  const nav = document.createElement("nav");
  nav.className = "tabbar";
  nav.setAttribute("aria-label", "Primary");

  for (const tab of TABS) {
    const a = document.createElement("a");
    a.className = "tabbar-item" + (tab.key === active ? " active" : "");
    a.href = tab.href;
    a.setAttribute("aria-label", tab.label);
    if (tab.key === active) a.setAttribute("aria-current", "page");
    a.innerHTML = `${tab.icon}<span>${tab.label}</span>`;
    nav.appendChild(a);
  }
  return nav;
}

/**
 * Convenience: mount header + tabbar on a root element. Most views just need
 * this two-line pattern.
 */
export function renderChrome(
  root: HTMLElement,
  active: TabKey,
  header: { title: string; subtitle?: string; showHorizon?: boolean },
): void {
  root.appendChild(renderPageHeader(header));
  // The tabbar is fixed-position so it can be appended once anywhere in the
  // tree. Appending into `root` (and replacing on route changes) keeps the
  // active state in sync without needing a global mount point.
  root.appendChild(renderTabBar(active));
}

/**
 * Format today's local date as a long editorial line (e.g. "Wednesday, May 13").
 */
export function formatTodayLong(d: Date = new Date()): string {
  return d.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

/* Legacy adapter — some views still call renderToolbar(active). Render a
   minimal title strip so they don't break. Today and chat have been migrated
   to renderPageHeader / renderTabBar directly. */
export function renderToolbar(active: TabKey): HTMLElement {
  const bar = document.createElement("header");
  bar.className = "toolbar";
  const title = document.createElement("strong");
  title.textContent = "Dispatch";
  bar.appendChild(title);
  const nav = renderTabBar(active);
  bar.appendChild(nav);
  return bar;
}

// ──────────────────────────────────────────────────────────────────────────

const ICON_TODAY = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="14" r="4"/><path d="M4 14h2M18 14h2M12 6v2M6.6 8.6l1.4 1.4M16 10l1.4-1.4"/><path d="M3 18h18"/></svg>`;
const ICON_ARCHIVE = `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="4" rx="1"/><path d="M5 9v9a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/></svg>`;
const ICON_CHAT = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 4V6a1 1 0 0 1 1-1Z"/></svg>`;
const ICON_RUNS = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.2 2"/></svg>`;

const TABS: Array<{ key: TabKey; label: string; href: string; icon: string }> = [
  { key: "today", label: "Today", href: "#/today", icon: ICON_TODAY },
  { key: "archive", label: "Archive", href: "#/archive", icon: ICON_ARCHIVE },
  { key: "chat", label: "Chat", href: "#/chat", icon: ICON_CHAT },
  { key: "runs", label: "Runs", href: "#/runs", icon: ICON_RUNS },
];
