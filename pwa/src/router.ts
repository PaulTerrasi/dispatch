// Tiny hash-based router so we don't need a router library on a PWA.

export type Route = {
  path: string; // e.g. "/today" or "/digest/:date"
  render: (params: Record<string, string>) => Promise<HTMLElement> | HTMLElement;
};

let _forceRefreshFn: (() => void) | null = null;

export function forceRefresh(): void {
  _forceRefreshFn?.();
}

export function startRouter(routes: Route[], mountEl: HTMLElement): void {
  const compiled = routes.map((r) => ({ ...r, regex: pathToRegex(r.path) }));
  let lastPath: string | null = null;

  _forceRefreshFn = () => {
    lastPath = null;
    void handle();
  };

  const handle = async () => {
    const path = location.hash.replace(/^#/, "") || "/today";
    if (path === lastPath) return;
    lastPath = path;
    for (const r of compiled) {
      const m = path.match(r.regex.regex);
      if (m) {
        const params: Record<string, string> = {};
        r.regex.keys.forEach((k, i) => {
          params[k] = decodeURIComponent(m[i + 1] ?? "");
        });
        const loader = document.createElement("div");
        loader.className = "page-loading";
        mountEl.replaceChildren(loader);

        try {
          const el = await r.render(params);
          if (path === lastPath) {
            mountEl.replaceChildren(el);
          }
        } catch (err) {
          console.error("[router] render error", err);
          const d = document.createElement("div");
          d.className = "empty";
          d.textContent = "Failed to load. Pull down to retry.";
          mountEl.replaceChildren(d);
        }
        return;
      }
    }
    mountEl.replaceChildren(notFound());
  };

  window.addEventListener("hashchange", handle);
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", handle, { once: true });
  } else {
    handle();
  }
}

function pathToRegex(path: string): { regex: RegExp; keys: string[] } {
  const keys: string[] = [];
  const pattern = path.replace(/:([a-zA-Z_]+)/g, (_m, k) => {
    keys.push(k);
    return "([^/]+)";
  });
  return { regex: new RegExp(`^${pattern}$`), keys };
}

function notFound(): HTMLElement {
  const div = document.createElement("div");
  div.className = "empty";
  div.textContent = "Page not found.";
  return div;
}

export function navigate(path: string): void {
  location.hash = path;
}
