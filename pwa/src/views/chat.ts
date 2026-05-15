import { api, type ChatStreamHandlers } from "../api";
import { prettyToolName } from "../tool-names";

type MsgItem = { kind: "msg"; role: "user" | "assistant"; text: string };
type ToolItem = {
  kind: "tool";
  id: string;
  name: string;
  label: string;
  input?: unknown;
  output?: string;
  status: "pending" | "ok" | "err";
};
type ReasoningItem = { kind: "reasoning"; text: string };
type Item = MsgItem | ToolItem | ReasoningItem;

const COLLAPSED_KEY = "chat:profile-collapsed";
const TURNS_KEY = "chat:turns";

function loadItems(): Item[] {
  try {
    const raw = localStorage.getItem(TURNS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const out: Item[] = [];
    for (const t of parsed) {
      if (!t || typeof t !== "object") continue;
      // Legacy shape: { role, text } — normalize to a msg item.
      if (
        t.kind === undefined &&
        (t.role === "user" || t.role === "assistant") &&
        typeof t.text === "string"
      ) {
        out.push({ kind: "msg", role: t.role, text: t.text });
        continue;
      }
      if (
        t.kind === "msg" &&
        (t.role === "user" || t.role === "assistant") &&
        typeof t.text === "string"
      ) {
        out.push({ kind: "msg", role: t.role, text: t.text });
      } else if (
        t.kind === "tool" &&
        typeof t.id === "string" &&
        typeof t.label === "string" &&
        (t.status === "pending" || t.status === "ok" || t.status === "err")
      ) {
        const item: ToolItem = {
          kind: "tool",
          id: t.id,
          name: typeof t.name === "string" ? t.name : "",
          label: t.label,
          status: t.status,
        };
        if (t.input !== undefined) item.input = t.input;
        if (typeof t.output === "string") item.output = t.output;
        out.push(item);
      }
      // Reasoning items are never persisted (see `persistItems`); any legacy
      // `{ kind: "reasoning" }` entries are silently dropped.
    }
    return out;
  } catch {
    return [];
  }
}

function persistItems(items: Item[]): void {
  try {
    // Reasoning is display-only — it doesn't ride along in the conversation
    // history sent to the model, and an extended-thinking turn can produce
    // tens of KB of deltas. Skip it to keep localStorage bounded and avoid
    // write amplification on every reasoning delta.
    const persistable = items.filter((it) => it.kind !== "reasoning");
    localStorage.setItem(TURNS_KEY, JSON.stringify(persistable));
  } catch {
    // ignore quota / serialization errors
  }
}

export async function renderChat(): Promise<HTMLElement> {
  // The chat page owns the full viewport (both panes always visible, each
  // scrolls independently). Tag the body so CSS can break the `main` 640px
  // cap; remove the tag when the route changes.
  document.body.classList.add("chat-page");
  const cleanupBodyClass = (): void => {
    document.body.classList.remove("chat-page");
    window.removeEventListener("hashchange", cleanupBodyClass);
  };
  window.addEventListener("hashchange", cleanupBodyClass);

  const root = document.createElement("div");
  root.className = "chat-root";
  const { bar: headerBar, profileToggleBtn } = renderChatHeader();
  root.appendChild(headerBar);

  const layout = document.createElement("div");
  layout.className = "chat-layout";
  root.appendChild(layout);

  // ── Chat panel ───────────────────────────────────────────────────────────
  // Chat is the primary child — always full-size. Profile is an overlay on top.
  const chatPanel = document.createElement("div");
  chatPanel.className = "chat-panel";
  layout.appendChild(chatPanel);

  // ── Backdrop (visible only when profile is expanded) ─────────────────────
  const backdrop = document.createElement("div");
  backdrop.className = "chat-profile-backdrop";
  backdrop.setAttribute("aria-hidden", "true");
  layout.appendChild(backdrop);

  // ── Profile panel (live read of profile.md) ──────────────────────────────
  const profilePanel = document.createElement("aside");
  profilePanel.className = "chat-profile";
  profilePanel.id = "chat-profile-panel";
  profileToggleBtn.setAttribute("aria-controls", "chat-profile-panel");
  const profileHeader = document.createElement("div");
  profileHeader.className = "chat-profile-header";
  const profileLabel = document.createElement("span");
  profileLabel.className = "chat-profile-label";
  profileLabel.textContent = "profile.md";
  const profileClose = document.createElement("button");
  profileClose.type = "button";
  profileClose.className = "chat-profile-close";
  profileClose.setAttribute("aria-label", "Close profile");
  profileClose.title = "Close";
  profileClose.textContent = "×";
  profileHeader.appendChild(profileLabel);
  profileHeader.appendChild(profileClose);
  const profileBody = document.createElement("div");
  profileBody.className = "chat-profile-body";
  profilePanel.appendChild(profileHeader);
  profilePanel.appendChild(profileBody);
  layout.appendChild(profilePanel);

  // ── Collapse / expand profile panel ─────────────────────────────────────
  const setCollapsed = (collapsed: boolean, returnFocus = false): void => {
    layout.classList.toggle("profile-collapsed", collapsed);
    profileToggleBtn.setAttribute("aria-expanded", String(!collapsed));
    profileToggleBtn.title = collapsed ? "Show profile.md" : "Hide profile.md";
    profileToggleBtn.classList.toggle("is-active", !collapsed);
    // When the drawer hides, the close button it owned slides off-screen —
    // bounce focus back to the visible toolbar control so keyboard users
    // don't get stranded.
    if (collapsed && returnFocus) profileToggleBtn.focus();
    try {
      localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch {
      // Safari private mode (and quota-exceeded states) throw on setItem.
      // The collapsed preference is a nice-to-have; losing it shouldn't
      // crash chat-view mount.
    }
  };
  // Default: collapsed. Only treat an explicit "0" as expanded.
  setCollapsed(localStorage.getItem(COLLAPSED_KEY) !== "0");
  profileToggleBtn.addEventListener("click", () => {
    setCollapsed(!layout.classList.contains("profile-collapsed"));
  });
  profileClose.addEventListener("click", () => setCollapsed(true, true));
  backdrop.addEventListener("click", () => setCollapsed(true, true));

  const refreshProfile = async (): Promise<void> => {
    try {
      const { markdown } = await api.profile();
      profileBody.innerHTML = renderMarkdown(markdown);
    } catch (e) {
      profileBody.textContent = `(could not load profile: ${(e as Error).message})`;
    }
  };
  void refreshProfile();

  const transcript = document.createElement("div");
  transcript.className = "chat-transcript";
  chatPanel.appendChild(transcript);

  const intro = document.createElement("div");
  intro.className = "note";
  intro.textContent =
    "Chat with the agent about tomorrow's digest. " +
    "It can edit the profile and source list directly — you'll see changes here.";
  transcript.appendChild(intro);

  const items: Item[] = loadItems();
  let activeStream: AbortController | null = null;

  const inputRow = document.createElement("div");
  inputRow.className = "chat-input chat-input-bar";
  const ta = document.createElement("textarea");
  ta.placeholder = "more woodworking, less AI hot takes…";
  const send = document.createElement("button");
  send.className = "primary";
  send.textContent = "Send";
  const reset = document.createElement("button");
  reset.className = "secondary";
  reset.textContent = "Reset";
  inputRow.appendChild(ta);
  inputRow.appendChild(send);
  inputRow.appendChild(reset);
  chatPanel.appendChild(inputRow);

  const appendMessage = (msg: MsgItem): HTMLElement => {
    const div = document.createElement("div");
    div.className = "chat-msg" + (msg.role === "user" ? " user" : "");
    div.textContent = msg.text;
    transcript.appendChild(div);
    div.scrollIntoView({ block: "end" });
    return div;
  };

  const appendTool = (tool: ToolItem): HTMLElement => {
    const card = document.createElement("div");
    card.className = `chat-tool-card ${tool.status}`;

    const header = document.createElement("button");
    header.type = "button";
    header.className = "chat-tool-card-header";

    const icon = document.createElement("span");
    icon.className = "chat-tool-card-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = toolIcon(tool.name);
    header.appendChild(icon);

    const label = document.createElement("span");
    label.className = "chat-tool-card-label";
    label.textContent = tool.label;
    header.appendChild(label);

    const statusEl = document.createElement("span");
    statusEl.className = "chat-tool-card-status";
    statusEl.textContent = toolStatusText(tool.status);
    header.appendChild(statusEl);

    const chevron = document.createElement("span");
    chevron.className = "chat-tool-card-chevron";
    chevron.setAttribute("aria-hidden", "true");
    chevron.textContent = "›";
    header.appendChild(chevron);

    card.appendChild(header);

    const body = document.createElement("div");
    body.className = "chat-tool-card-body";
    card.appendChild(body);
    renderToolBody(body, tool);

    header.addEventListener("click", () => {
      card.classList.toggle("expanded");
    });

    transcript.appendChild(card);
    card.scrollIntoView({ block: "end" });
    return card;
  };

  const updateToolCard = (card: HTMLElement, tool: ToolItem): void => {
    card.classList.remove("pending", "ok", "err");
    card.classList.add(tool.status);
    const statusEl = card.querySelector(".chat-tool-card-status");
    if (statusEl) statusEl.textContent = toolStatusText(tool.status);
    const body = card.querySelector(".chat-tool-card-body") as HTMLElement | null;
    if (body) renderToolBody(body, tool);
  };

  const appendReasoning = (item: ReasoningItem): HTMLElement => {
    const card = document.createElement("div");
    card.className = "chat-reasoning";

    const header = document.createElement("button");
    header.type = "button";
    header.className = "chat-reasoning-header";

    const chevron = document.createElement("span");
    chevron.className = "chat-reasoning-chevron";
    chevron.setAttribute("aria-hidden", "true");
    chevron.textContent = "›";
    header.appendChild(chevron);

    const label = document.createElement("span");
    label.className = "chat-reasoning-label";
    label.textContent = "Thinking";
    header.appendChild(label);

    card.appendChild(header);

    const body = document.createElement("div");
    body.className = "chat-reasoning-body";
    body.textContent = item.text;
    card.appendChild(body);

    header.addEventListener("click", () => {
      card.classList.toggle("expanded");
    });

    transcript.appendChild(card);
    card.scrollIntoView({ block: "end" });
    return card;
  };

  // Loading indicator: three pulsing dots, shown whenever the agent is
  // working but not currently streaming text or reasoning. Created once
  // and re-parented to the end of the transcript when needed.
  let loadingEl: HTMLElement | null = null;
  const ensureLoading = (): void => {
    if (!loadingEl) {
      loadingEl = document.createElement("div");
      loadingEl.className = "chat-loading";
      loadingEl.setAttribute("aria-label", "Agent is working");
      for (let i = 0; i < 3; i++) {
        const dot = document.createElement("span");
        dot.className = "chat-loading-dot";
        loadingEl.appendChild(dot);
      }
    }
    transcript.appendChild(loadingEl);
    loadingEl.scrollIntoView({ block: "end" });
  };
  const hideLoading = (): void => {
    if (loadingEl && loadingEl.parentElement) loadingEl.remove();
  };

  // Replay persisted conversation, if any.
  for (const it of items) {
    if (it.kind === "msg") {
      appendMessage(it);
    } else if (it.kind === "tool") {
      appendTool(it);
    }
    // Reasoning items are never persisted (see `persistItems`); they can
    // only enter `items` during the live stream, never via replay.
  }

  const onSend = (): void => {
    const text = ta.value.trim();
    if (!text || activeStream) return;
    const userMsg: MsgItem = { kind: "msg", role: "user", text };
    appendMessage(userMsg);
    items.push(userMsg);
    persistItems(items);
    ta.value = "";
    send.disabled = true;

    // Snapshot history (messages only) before any agent output arrives. The
    // backend expects history to end with a user turn.
    const requestHistory = items
      .filter((it): it is MsgItem => it.kind === "msg")
      .map(({ role, text }) => ({ role, text }));

    // Lazy assistant / reasoning cursors. We don't create the assistant
    // bubble up front — only when text actually starts streaming — so a
    // tool call before any output doesn't leave an empty bubble behind.
    let currentAssistantMsg: MsgItem | null = null;
    let currentAssistantEl: HTMLElement | null = null;
    let currentReasoningItem: ReasoningItem | null = null;
    let currentReasoningEl: HTMLElement | null = null;
    const toolCards = new Map<string, { el: HTMLElement; item: ToolItem }>();

    const finalizeAssistant = (): void => {
      if (currentAssistantEl) currentAssistantEl.classList.remove("streaming");
      currentAssistantMsg = null;
      currentAssistantEl = null;
    };
    const finalizeReasoning = (): void => {
      if (currentReasoningEl) currentReasoningEl.classList.remove("streaming");
      currentReasoningItem = null;
      currentReasoningEl = null;
    };

    ensureLoading();

    const handlers: ChatStreamHandlers = {
      onText: (delta) => {
        finalizeReasoning();
        hideLoading();
        if (!currentAssistantMsg) {
          currentAssistantMsg = { kind: "msg", role: "assistant", text: "" };
          items.push(currentAssistantMsg);
          currentAssistantEl = appendMessage(currentAssistantMsg);
          currentAssistantEl.classList.add("streaming");
        }
        currentAssistantMsg.text += delta;
        currentAssistantEl!.textContent = currentAssistantMsg.text;
        currentAssistantEl!.scrollIntoView({ block: "end" });
        persistItems(items);
      },
      onReasoning: (delta) => {
        finalizeAssistant();
        hideLoading();
        if (!currentReasoningItem) {
          currentReasoningItem = { kind: "reasoning", text: "" };
          items.push(currentReasoningItem);
          currentReasoningEl = appendReasoning(currentReasoningItem);
          currentReasoningEl.classList.add("streaming");
        }
        currentReasoningItem.text += delta;
        const body = currentReasoningEl!.querySelector(
          ".chat-reasoning-body",
        ) as HTMLElement | null;
        if (body) body.textContent = currentReasoningItem.text;
        currentReasoningEl!.scrollIntoView({ block: "end" });
        persistItems(items);
      },
      onToolStart: (tool) => {
        finalizeAssistant();
        finalizeReasoning();
        const label = prettyToolStart(tool.name, tool.input);
        const toolItem: ToolItem = {
          kind: "tool",
          id: tool.id,
          name: tool.name,
          label,
          input: tool.input,
          status: "pending",
        };
        items.push(toolItem);
        const card = appendTool(toolItem);
        toolCards.set(tool.id, { el: card, item: toolItem });
        persistItems(items);
        ensureLoading();
      },
      onToolEnd: (tool) => {
        const entry = toolCards.get(tool.tool_use_id);
        if (entry) {
          entry.item.status = tool.ok ? "ok" : "err";
          if (typeof tool.output === "string") entry.item.output = tool.output;
          updateToolCard(entry.el, entry.item);
          persistItems(items);
        }
        ensureLoading();
      },
      onProfileChanged: () => {
        void refreshProfile();
      },
      onDone: () => {
        finalizeAssistant();
        finalizeReasoning();
        hideLoading();
        persistItems(items);
        finish();
      },
      onError: (message) => {
        finalizeAssistant();
        finalizeReasoning();
        hideLoading();
        const errEl = document.createElement("div");
        errEl.className = "chat-error";
        errEl.textContent = `error: ${message}`;
        transcript.appendChild(errEl);
        errEl.scrollIntoView({ block: "end" });
        persistItems(items);
        finish();
      },
    };

    const finish = (): void => {
      activeStream = null;
      send.disabled = false;
      ta.focus();
    };

    activeStream = api.chatStream(requestHistory, handlers);
  };

  const onReset = (): void => {
    if (activeStream) {
      activeStream.abort();
      activeStream = null;
    }
    items.length = 0;
    localStorage.removeItem(TURNS_KEY);
    hideLoading();
    transcript.replaceChildren(intro);
    send.disabled = false;
    ta.focus();
  };

  send.onclick = () => onSend();
  reset.onclick = () => onReset();
  ta.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  return root;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function renderChatHeader(): { bar: HTMLElement; profileToggleBtn: HTMLButtonElement } {
  const bar = document.createElement("header");
  bar.className = "toolbar chat-toolbar";
  const title = document.createElement("strong");
  title.textContent = "dispatch chat";
  bar.appendChild(title);

  const right = document.createElement("div");
  right.className = "chat-toolbar-actions";

  const profileToggleBtn = document.createElement("button");
  profileToggleBtn.type = "button";
  profileToggleBtn.className = "chat-profile-btn";
  profileToggleBtn.textContent = "profile.md";
  right.appendChild(profileToggleBtn);

  const back = document.createElement("a");
  back.href = "#/today";
  back.className = "chat-back";
  back.textContent = "today ›";
  back.setAttribute("aria-label", "Back to today");
  right.appendChild(back);

  bar.appendChild(right);
  return { bar, profileToggleBtn };
}

function prettyToolStart(name: string, _input: unknown): string {
  switch (name) {
    case "patch_profile":
      return "editing profile.md";
    case "add_source":
      return "adding source";
    case "remove_source":
      return "removing source";
    case "read_profile":
      return "reading profile";
    case "read_recent_feedback":
      return "reading recent feedback";
    case "read_recent_digests":
      return "reading recent digests";
    case "read_recent_curation_runs":
      return "reading curation runs";
    case "list_sources":
      return "listing sources";
    case "end_reflection":
      return "wrapping up";
    default:
      return prettyToolName(name) || "tool call";
  }
}

function toolIcon(name: string): string {
  switch (name) {
    case "patch_profile":
      return "✏️";
    case "add_source":
      return "➕";
    case "remove_source":
      return "➖";
    case "end_reflection":
      return "✓";
    case "read_profile":
    case "read_recent_feedback":
    case "read_recent_digests":
    case "read_recent_curation_runs":
    case "list_sources":
      return "👀";
    default:
      return "·";
  }
}

function toolStatusText(status: ToolItem["status"]): string {
  switch (status) {
    case "pending":
      return "running…";
    case "ok":
      return "done";
    case "err":
      return "failed";
  }
}

function renderToolBody(body: HTMLElement, tool: ToolItem): void {
  body.replaceChildren();
  const inputStr = formatToolInput(tool.input);
  if (inputStr) {
    body.appendChild(renderToolSection("Input", inputStr));
  }
  if (tool.output) {
    body.appendChild(renderToolSection("Output", tool.output));
  }
  if (!inputStr && !tool.output) {
    const empty = document.createElement("div");
    empty.className = "chat-tool-card-empty";
    empty.textContent = "(no details)";
    body.appendChild(empty);
  }
}

function renderToolSection(label: string, content: string): HTMLElement {
  const section = document.createElement("div");
  section.className = "chat-tool-card-section";
  const lab = document.createElement("div");
  lab.className = "chat-tool-card-section-label";
  lab.textContent = label;
  section.appendChild(lab);
  const pre = document.createElement("pre");
  pre.className = "chat-tool-card-section-content";
  pre.textContent = content;
  section.appendChild(pre);
  return section;
}

function formatToolInput(input: unknown): string {
  if (input === undefined || input === null) return "";
  if (typeof input === "string") return input;
  // Inputs come from the SDK (already JSON-deserialized), so JSON.stringify
  // can't hit a circular ref. Empty `{}` / `[]` carry no useful detail.
  const s = JSON.stringify(input, null, 2);
  if (s === "{}" || s === "[]") return "";
  return s;
}

// Tiny dependency-free markdown renderer for profile.md content.
// Handles: # / ## headings, - / * unordered lists, blank lines, inline `code`,
// and paragraphs. Anything else falls through as plain text. Output is
// HTML-escaped before formatting tokens are applied.
function renderMarkdown(src: string): string {
  const lines = src.split("\n");
  const out: string[] = [];
  let inList = false;
  let paragraph: string[] = [];

  const flushParagraph = (): void => {
    if (paragraph.length) {
      out.push(`<p>${formatInline(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  };
  const closeList = (): void => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (!line) {
      flushParagraph();
      closeList();
      continue;
    }
    const h2 = /^##\s+(.*)$/.exec(line);
    const h1 = /^#\s+(.*)$/.exec(line);
    const li = /^[-*]\s+(.*)$/.exec(line);
    if (h2) {
      flushParagraph();
      closeList();
      out.push(`<h3>${formatInline(h2[1])}</h3>`);
    } else if (h1) {
      flushParagraph();
      closeList();
      out.push(`<h2>${formatInline(h1[1])}</h2>`);
    } else if (li) {
      flushParagraph();
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${formatInline(li[1])}</li>`);
    } else {
      closeList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  closeList();
  return out.join("\n") || '<p class="muted">(profile is empty)</p>';
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatInline(s: string): string {
  // Escape first, then re-introduce inline-code spans.
  return escapeHtml(s).replace(/`([^`]+)`/g, "<code>$1</code>");
}
