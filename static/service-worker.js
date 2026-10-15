const CACHE_NAME = "itmsu-cache-v4";
const ASSETS = [
  "/",
  "/static/manifest.json",
  "/static/css/app.css",
  "https://cdn.jsdelivr.net/npm/chart.js",
  "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
  "https://cdn.jsdelivr.net/npm/lucide/dist/umd/lucide.min.js"
];

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    })
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // Always hit the network for APIs, uploads, and SPA page navigations
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/uploads/") ||
    e.request.mode === "navigate"
  ) {
    return;
  }

  e.respondWith(
    caches.match(e.request).then((cachedResponse) => {
      return cachedResponse || fetch(e.request).then((response) => {
        if (
          response.status === 200 &&
          e.request.method === "GET" &&
          url.pathname.startsWith("/static/")
        ) {
          const cacheCopy = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(e.request, cacheCopy);
          });
        }
        return response;
      });
    })
  );
});
