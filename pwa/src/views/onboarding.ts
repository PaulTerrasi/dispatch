import { api } from "../api";

interface Turn {
  role: "user" | "assistant";
  text: string;
}

export async function renderOnboarding(): Promise<HTMLElement> {
  const root = document.createElement("div");

  const header = document.createElement("header");
  header.className = "page-header";
  const wordmark = document.createElement("div");
  wordmark.className = "page-wordmark";
  wordmark.textContent = "dispatch";
  header.appendChild(wordmark);
  const title = document.createElement("h1");
  title.className = "page-title";
  title.innerHTML = `Let's get to <em>know you</em>.`;
  header.appendChild(title);
  const sub = document.createElement("p");
  sub.className = "page-subtitle";
  sub.textContent = "A short conversation, then I'll draft your profile.";
  header.appendChild(sub);
  const hr = document.createElement("hr");
  hr.className = "horizon-rule";
  header.appendChild(hr);
  root.appendChild(header);

  const transcript = document.createElement("div");
  root.appendChild(transcript);

  const turns: Turn[] = [
    {
      role: "assistant",
      text: "What do you find yourself reading lately, and what do you wish you " + "had time for?",
    },
  ];
  const renderTurn = (t: Turn) => {
    const div = document.createElement("div");
    div.className = "chat-msg" + (t.role === "user" ? " user" : "");
    div.textContent = t.text;
    transcript.appendChild(div);
  };
  turns.forEach(renderTurn);

  const draftWrap = document.createElement("div");
  draftWrap.style.marginTop = "16px";
  root.appendChild(draftWrap);

  const inputRow = document.createElement("div");
  inputRow.className = "chat-input";
  const ta = document.createElement("textarea");
  const send = document.createElement("button");
  send.className = "primary";
  send.textContent = "Send";
  inputRow.appendChild(ta);
  inputRow.appendChild(send);
  root.appendChild(inputRow);

  const onSend = async (): Promise<void> => {
    const text = ta.value.trim();
    if (!text) return;
    const userTurn: Turn = { role: "user", text };
    turns.push(userTurn);
    renderTurn(userTurn);
    ta.value = "";
    send.disabled = true;
    try {
      const r = await api.onboardingMessage(turns);
      if (r.reply) {
        const t: Turn = { role: "assistant", text: r.reply };
        turns.push(t);
        renderTurn(t);
      }
      if (r.proposed_profile) {
        renderDraft(draftWrap, r.proposed_profile);
      }
    } finally {
      send.disabled = false;
      ta.focus();
    }
  };

  send.onclick = () => void onSend();
  ta.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void onSend();
    }
  };

  return root;
}

function renderDraft(into: HTMLElement, markdown: string): void {
  into.replaceChildren();
  const h = document.createElement("h2");
  h.textContent = "Draft profile";
  into.appendChild(h);

  const ta = document.createElement("textarea");
  ta.value = markdown;
  ta.style.width = "100%";
  ta.style.minHeight = "200px";
  ta.style.background = "var(--bg-elev)";
  ta.style.color = "var(--fg)";
  ta.style.border = "1px solid var(--border)";
  ta.style.borderRadius = "8px";
  ta.style.padding = "10px";
  ta.style.fontFamily = "ui-monospace, SFMono-Regular, monospace";
  into.appendChild(ta);

  const save = document.createElement("button");
  save.className = "primary";
  save.textContent = "Save profile";
  save.style.marginTop = "8px";
  save.onclick = async () => {
    save.disabled = true;
    save.textContent = "Saving…";
    await api.saveProfile(ta.value);
    location.hash = "/today";
    location.reload();
  };
  into.appendChild(save);
}
