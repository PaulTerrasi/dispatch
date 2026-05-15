import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderDigestDay } from "../views/digest_day";

beforeEach(() => {
  document.body.innerHTML = "";
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function stub(routes: Record<string, unknown | { status: number }>) {
  vi.stubGlobal(
    "fetch",
    vi.fn<(input: RequestInfo | URL) => Promise<Response>>(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      for (const [path, body] of Object.entries(routes)) {
        if (url.startsWith(path)) {
          if (body && typeof body === "object" && "status" in body) {
            return new Response("", { status: (body as { status: number }).status });
          }
          return new Response(JSON.stringify(body), { status: 200 });
        }
      }
      return new Response("[]", { status: 200 });
    }),
  );
}

describe("renderDigestDay", () => {
  it('renders "No digest" when the API returns 404', async () => {
    stub({ "/api/digest/2026-01-01": { status: 404 } });
    const el = await renderDigestDay({ date: "2026-01-01" });
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("No digest for 2026-01-01");
  });

  it('renders "No reacted items" when the digest exists but has zero items', async () => {
    stub({ "/api/digest/2026-05-14": { date: "2026-05-14", items: [], agent_notes: "" } });
    const el = await renderDigestDay({ date: "2026-05-14" });
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("No reacted items");
  });

  it("renders the digest body and a back-to-archive link when items exist", async () => {
    stub({
      "/api/digest/2026-05-14": {
        date: "2026-05-14",
        items: [
          {
            id: "abc",
            type: "article",
            title: "An article",
            source: "Example",
            url: "https://example.com/a",
            summary: "summary",
            feedback: "up",
          },
        ],
        agent_notes: "two keepers",
        runs: [],
      },
    });
    const el = await renderDigestDay({ date: "2026-05-14" });
    document.body.appendChild(el);
    expect(document.body.textContent).toContain("An article");
    expect((document.querySelector(".back-link") as HTMLAnchorElement).href).toContain("/archive");
  });

  it("subtitle shows the reacted-item count when present", async () => {
    stub({
      "/api/digest/2026-05-14": {
        date: "2026-05-14",
        items: [{ id: "a", type: "article", title: "T", source: "s", url: "u", summary: "" }],
        agent_notes: "",
        runs: [],
      },
    });
    const el = await renderDigestDay({ date: "2026-05-14" });
    document.body.appendChild(el);
    const subtitle = document.querySelector(".page-subtitle") as HTMLElement;
    expect(subtitle.textContent).toContain("1 reacted items");
  });
});
