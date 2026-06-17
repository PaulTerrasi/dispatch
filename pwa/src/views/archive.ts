import { api, type SearchHit } from "../api";
import { renderPageHeader, renderTabBar } from "./_toolbar";
import { formatDayLong } from "../time";

export async function renderArchive(): Promise<HTMLElement> {
  const root = document.createElement("div");
  root.appendChild(
    renderPageHeader({
      title: "Archive",
      subtitle: "Every past digest, searchable.",
    }),
  );

  const search = document.createElement("div");
  search.className = "search-box";
  const input = document.createElement("input");
  input.type = "search";
  input.placeholder = "Search across all digests…";
  const btn = document.createElement("button");
  btn.textContent = "Search";
  search.appendChild(input);
  search.appendChild(btn);
  root.appendChild(search);

  const results = document.createElement("div");
  root.appendChild(results);

  const dateList = document.createElement("div");
  root.appendChild(dateList);

  const summaries = await api.list();
  if (!summaries.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No digests yet.";
    dateList.appendChild(empty);
  } else {
    for (const s of summaries) {
      const row = document.createElement("a");
      row.className = "archive-row";
      row.href = `#/digest/${s.date}`;
      const left = document.createElement("span");
      left.textContent = formatDayLong(s.date, { year: true });
      const right = document.createElement("span");
      right.textContent = `${s.item_count} item${s.item_count === 1 ? "" : "s"}`;
      right.style.color = "var(--muted)";
      right.style.fontSize = "0.85rem";
      row.appendChild(left);
      row.appendChild(right);
      dateList.appendChild(row);
    }
  }

  const doSearch = async () => {
    const q = input.value.trim();
    results.replaceChildren();
    dateList.hidden = !!q;
    if (!q) return;
    const hits = await api.search(q);
    if (!hits.length) {
      const e = document.createElement("div");
      e.className = "empty";
      e.textContent = "No matches.";
      results.appendChild(e);
      return;
    }
    for (const h of hits) {
      results.appendChild(renderHit(h));
    }
  };
  btn.onclick = () => void doSearch();
  input.onkeydown = (e) => {
    if (e.key === "Enter") void doSearch();
  };

  root.appendChild(renderTabBar("archive"));
  return root;
}

function renderHit(hit: SearchHit): HTMLElement {
  const a = document.createElement("a");
  a.className = "item";
  a.href = `#/digest/${hit.date}`;
  a.style.display = "block";
  a.style.color = "inherit";

  const title = document.createElement("h2");
  title.textContent = hit.title;
  a.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = formatDayLong(hit.date, { year: true });
  a.appendChild(meta);

  if (hit.snippet) {
    const p = document.createElement("p");
    p.textContent = hit.snippet;
    a.appendChild(p);
  }
  return a;
}
