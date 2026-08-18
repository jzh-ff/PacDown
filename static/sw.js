/* PacDown Service Worker：仅缓存应用外壳（stale-while-revalidate），绝不缓存 /api */
const CACHE = "pacdown-shell-v1";
const SHELL = [
  "/",
  "/static/css/design.css",
  "/static/js/app.js",
  "/static/manifest.webmanifest",
  "/static/assets/icon-192.png",
  "/static/assets/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api")) return; // API 直连
  if (!SHELL.includes(url.pathname)) return;                                // 其余资源直连
  e.respondWith(
    caches.match(e.request).then((cached) => {
      const fresh = fetch(e.request)
        .then((resp) => {
          if (resp.ok) caches.open(CACHE).then((c) => c.put(e.request, resp.clone()));
          return resp;
        })
        .catch(() => cached);
      return cached || fresh;
    })
  );
});
