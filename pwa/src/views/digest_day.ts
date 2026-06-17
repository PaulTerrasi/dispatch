import { api } from "../api";
import { renderDigest } from "./today";
import { renderPageHeader, renderTabBar } from "./_toolbar";
import { formatDayLong } from "../time";

export async function renderDigestDay(params: Record<string, string>): Promise<HTMLElement> {
  const root = document.createElement("div");
  const date = params.date;
  const digest = await api.byDate(date, true);

  root.appendChild(
    renderPageHeader({
      title: formatDayLong(date),
      subtitle:
        digest && digest.items.length
          ? `${digest.items.length} reacted item${digest.items.length === 1 ? "" : "s"}`
          : undefined,
    }),
  );

  const back = document.createElement("a");
  back.className = "back-link";
  back.href = "#/archive";
  back.textContent = "back to archive";
  root.appendChild(back);

  if (!digest) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = `No digest for ${date}.`;
    root.appendChild(empty);
  } else if (!digest.items.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No reacted items for this day.";
    root.appendChild(empty);
  } else {
    root.appendChild(renderDigest(digest));
  }

  root.appendChild(renderTabBar("archive"));
  return root;
}
