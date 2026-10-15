// Offline-capable without ever serving stale app code.
//
// - The app's own files (/, the SPA routes, /static/*) are NETWORK-FIRST: every
//   load gets the deployed version, and the cached copy is only used offline.
//   (They used to be cache-first forever, so users ran old JS after a deploy
//   until CACHE_NAME was bumped by hand.)
// - CDN libraries are pinned to exact versions (with SRI in index.html), so
//   they never change and are served CACHE-FIRST.
// - /api/* and anything non-GET always go straight to the network.
const CACHE_NAME = "itmsu-cache-v7";
const CDN_ASSETS = [
  "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js",
  "https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js",
  "https://cdn.jsdelivr.net/npm/lucide@1.57.0/dist/umd/lucide.min.js",
  "https://cdn.jsdelivr.net/npm/dompurify@3.4.17/dist/purify.min.js",
];

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      // CORS mode so the cached responses satisfy the SRI checks.
      Promise.all(CDN_ASSETS.map((url) =>
        fetch(url, { mode: "cors" }).then((res) => res.ok && cache.put(url, res)).catch(() => {})
      ))
    )
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function networkFirst(request) {
  // cache: "no-cache" revalidates with the server (304 via ETag) instead of
  // trusting the browser's HTTP cache, which a CDN may have told to keep files
  // for hours. Navigations cannot be re-issued with a new mode, so they go as-is.
  const fresh = request.mode === "navigate" ? request : new Request(request, { cache: "no-cache" });
  return fetch(fresh)
    .then((response) => {
      if (response.ok && response.type === "basic") {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) =>
          // Assets are requested as "?v=<content hash>", a new URL per deploy;
          // drop the copies of older versions so the cache does not grow forever.
          cache.keys(request, { ignoreSearch: true })
            .then((keys) => Promise.all(keys.filter((k) => k.url !== request.url).map((k) => cache.delete(k))))
            .then(() => cache.put(request, copy)));
      }
      return response;
    })
    .catch(() => caches.match(request).then((cached) => cached || Response.error()));
}

function cacheFirst(request) {
  return caches.match(request).then((cached) => cached || fetch(request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
    }
    return response;
  }));
}

self.addEventListener("fetch", (e) => {
  const request = e.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/uploads/")) return;
    if (request.mode === "navigate" || url.pathname.startsWith("/static/")) {
      e.respondWith(networkFirst(request));
    }
    return;
  }
  if (CDN_ASSETS.includes(request.url)) {
    e.respondWith(cacheFirst(request));
  }
});
