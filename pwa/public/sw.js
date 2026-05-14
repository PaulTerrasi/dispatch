// Minimal service worker.
// Strategy:
//   - network-first for navigation requests (HTML) so deploys are picked up
//     on the next page load; falls back to cached index.html when offline
//   - network-first for /api/* with a cache fallback so a stale-but-readable
//     digest is available when offline
//   - cache-first for everything else (assets are content-hashed by Vite, so
//     they're immutable and safe to cache aggressively)

const SHELL_CACHE = "digest-shell-v3";
const API_CACHE = "digest-api-v1";
const SHELL_URLS = ["/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_URLS)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== SHELL_CACHE && k !== API_CACHE)
          .map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(req));
    return;
  }
  if (req.mode === "navigate" || req.destination === "document") {
    event.respondWith(networkFirstHTML(req));
    return;
  }
  event.respondWith(cacheFirst(req));
});

async function networkFirst(req) {
  try {
    const fresh = await fetch(req);
    if (fresh.ok) {
      const clone = fresh.clone();
      caches.open(API_CACHE).then((c) => c.put(req, clone));
    }
    return fresh;
  } catch (_e) {
    const cached = await caches.match(req);
    if (cached) return cached;
    return new Response(JSON.stringify({ offline: true }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}

async function networkFirstHTML(req) {
  try {
    const fresh = await fetch(req);
    if (fresh.ok) {
      const clone = fresh.clone();
      caches.open(SHELL_CACHE).then((c) => c.put("/index.html", clone));
    }
    return fresh;
  } catch (_e) {
    const cached = await caches.match("/index.html");
    if (cached) return cached;
    return new Response("offline", {
      status: 503,
      headers: { "Content-Type": "text/plain" },
    });
  }
}

async function cacheFirst(req) {
  const cached = await caches.match(req);
  if (cached) return cached;
  try {
    const fresh = await fetch(req);
    if (fresh.ok) {
      const clone = fresh.clone();
      caches.open(SHELL_CACHE).then((c) => c.put(req, clone));
    }
    return fresh;
  } catch (_e) {
    return caches.match("/index.html");
  }
}
