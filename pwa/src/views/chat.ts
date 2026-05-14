import { api, type TalkStreamHandlers } from "../api";

interface Turn {
  role: "user" | "assistant";
  text: string;
}

const COLLAPSED_KEY = "talk:profile-collapsed";
const TURNS_KEY = "talk:turns";

function loadTurns(): Turn[] {
  try {
    const raw = localStorage.getItem(TURNS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (t): t is Turn =>
        t &&
        typeof t === "object" &&
        (t.role === "user" || t.role === "assistant") &&
        typeof t.text === "string",
    );
  } catch {
    return [];
  }
}

function persistTurns(turns: Turn[]): void {
  try {
    localStorage.setItem(TURNS_KEY, JSON.stringify(turns));
  } catch {
    // ignore quota / serialization errors
  }
}

export async function renderChat(): Promise<HTMLElement> {
  // The talk page owns the full viewport (both panes always visible, each
  // scrolls independently). Tag the body so CSS can break the `main` 640px
  // cap; remove the tag when the route changes.
  document.body.classList.add("talk-page");
  const cleanupBodyClass = (): void => {
    document.body.classList.remove("talk-page");
    window.removeEventListener("hashchange", cleanupBodyClass);
  };
  window.addEventListener("hashchange", cleanupBodyClass);

  const root = document.createElement("div");
  root.className = "talk-root";
  root.appendChild(renderTalkHeader());

  const layout = document.createElement("div");
  layout.className = "talk-layout";
  root.appendChild(layout);

  // ── Chat panel ───────────────────────────────────────────────────────────
  // Chat is the primary child — always full-size. Profile is an overlay on top.
  const chatPanel = document.createElement("div");
  chatPanel.className = "talk-chat";
  layout.appendChild(chatPanel);

  // ── Backdrop (visible only when profile is expanded) ─────────────────────
  const backdrop = document.createElement("div");
  backdrop.className = "talk-profile-backdrop";
  backdrop.setAttribute("aria-hidden", "true");
  layout.appendChild(backdrop);

  // ── Profile panel (live read of profile.md) ──────────────────────────────
  const profilePanel = document.createElement("aside");
  profilePanel.className = "talk-profile";
  const profileHeader = document.createElement("button");
  profileHeader.type = "button";
  profileHeader.className = "talk-profile-header";
  const profileLabel = document.createElement("span");
  profileLabel.className = "talk-profile-label";
  profileLabel.textContent = "profile.md";
  const profileToggle = document.createElement("span");
  profileToggle.className = "talk-profile-toggle";
  profileToggle.setAttribute("aria-hidden", "true");
  profileHeader.appendChild(profileLabel);
  profileHeader.appendChild(profileToggle);
  const profileBody = document.createElement("div");
  profileBody.className = "talk-profile-body";
  profilePanel.appendChild(profileHeader);
  profilePanel.appendChild(profileBody);
  layout.appendChild(profilePanel);

  // ── Collapse / expand profile panel ─────────────────────────────────────
  const setCollapsed = (collapsed: boolean): void => {
    layout.classList.toggle("profile-collapsed", collapsed);
    profileHeader.setAttribute("aria-expanded", String(!collapsed));
    profileHeader.title = collapsed ? "Show profile.md" : "Hide profile.md";
    profileToggle.textContent = collapsed ? "›" : "‹";
    localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  };
  // Default: collapsed. Only treat an explicit "0" as expanded.
  setCollapsed(localStorage.getItem(COLLAPSED_KEY) !== "0");
  profileHeader.addEventListener("click", () => {
    setCollapsed(!layout.classList.contains("profile-collapsed"));
  });
  backdrop.addEventListener("click", () => setCollapsed(true));

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
  transcript.className = "talk-transcript";
  chatPanel.appendChild(transcript);

  const intro = document.createElement("div");
  intro.className = "note";
  intro.textContent =
    "Talk to the agent about tomorrow's digest. " +
    "It can edit the profile and source list directly — you'll see changes here.";
  transcript.appendChild(intro);

  const turns: Turn[] = loadTurns();
  let activeStream: AbortController | null = null;

  const inputRow = document.createElement("div");
  inputRow.className = "chat-input talk-input";
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

  const appendMessage = (turn: Turn): HTMLElement => {
    const div = document.createElement("div");
    div.className = "chat-msg" + (turn.role === "user" ? " user" : "");
    div.textContent = turn.text;
    transcript.appendChild(div);
    div.scrollIntoView({ block: "end" });
    return div;
  };

  // Replay persisted conversation, if any.
  for (const t of turns) appendMessage(t);

  const appendStatus = (text: string): HTMLElement => {
    const pill = document.createElement("div");
    pill.className = "talk-tool-pill";
    pill.textContent = text;
    transcript.appendChild(pill);
    pill.scrollIntoView({ block: "end" });
    return pill;
  };

  const onSend = (): void => {
    const text = ta.value.trim();
    if (!text || activeStream) return;
    appendMessage({ role: "user", text });
    turns.push({ role: "user", text });
    persistTurns(turns);
    ta.value = "";
    send.disabled = true;

    // The backend expects history to end with a user turn — snapshot it now
    // before we append the assistant placeholder below.
    const requestHistory = turns.slice();

    // Push the assistant placeholder into `turns` up-front so partial replies
    // are persisted incrementally — a mid-stream reload (or a missing `done`
    // event) still preserves whatever text streamed in.
    const assistantTurn: Turn = { role: "assistant", text: "" };
    turns.push(assistantTurn);
    const assistantEl = appendMessage(assistantTurn);
    const toolPills = new Map<string, HTMLElement>();

    const dropEmptyAssistant = (): void => {
      if (turns[turns.length - 1] === assistantTurn && !assistantTurn.text) {
        turns.pop();
        assistantEl.remove();
      }
    };

    const handlers: TalkStreamHandlers = {
      onText: (delta) => {
        assistantTurn.text += delta;
        assistantEl.textContent = assistantTurn.text;
        assistantEl.scrollIntoView({ block: "end" });
        persistTurns(turns);
      },
      onToolStart: (tool) => {
        const pill = appendStatus(prettyToolStart(tool.name, tool.input));
        toolPills.set(tool.id, pill);
      },
      onToolEnd: (tool) => {
        const pill = toolPills.get(tool.tool_use_id);
        if (pill) {
          pill.classList.add(tool.ok ? "ok" : "err");
        }
      },
      onProfileChanged: () => {
        void refreshProfile();
      },
      onDone: () => {
        dropEmptyAssistant();
        persistTurns(turns);
        finish();
      },
      onError: (message) => {
        appendStatus(`error: ${message}`).classList.add("err");
        dropEmptyAssistant();
        persistTurns(turns);
        finish();
      },
    };

    const finish = (): void => {
      activeStream = null;
      send.disabled = false;
      ta.focus();
    };

    activeStream = api.talkStream(requestHistory, handlers);
  };

  const onReset = (): void => {
    if (activeStream) {
      activeStream.abort();
      activeStream = null;
    }
    turns.length = 0;
    localStorage.removeItem(TURNS_KEY);
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

function renderTalkHeader(): HTMLElement {
  const bar = document.createElement("header");
  bar.className = "toolbar talk-toolbar";
  const title = document.createElement("strong");
  title.textContent = "dispatch chat";
  bar.appendChild(title);
  const back = document.createElement("a");
  back.href = "#/today";
  back.className = "talk-back";
  back.textContent = "today ›";
  back.setAttribute("aria-label", "Back to today");
  bar.appendChild(back);
  return bar;
}

function prettyToolStart(name: string, _input: unknown): string {
  switch (name) {
    case "patch_profile":
      return "✏️ editing profile.md…";
    case "add_source":
      return "➕ adding source…";
    case "remove_source":
      return "➖ removing source…";
    case "read_profile":
      return "👀 reading profile…";
    case "read_recent_feedback":
      return "👀 reading recent feedback…";
    case "read_recent_digests":
      return "👀 reading recent digests…";
    case "read_recent_curation_runs":
      return "👀 reading curation runs…";
    case "list_sources":
      return "👀 listing sources…";
    case "end_reflection":
      return "✓ wrapping up";
    default:
      return `· ${name}`;
  }
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
