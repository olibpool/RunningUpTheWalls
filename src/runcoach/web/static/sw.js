// Coachal service worker: everything network-first so a CSS/JS update shows straight away
// (cache-first served old CSS against new pages), falling back to the cache when offline.
const CACHE = "coachal-v6";
const SHELL = ["/static/app.css", "/static/app.js", "/static/vendor/htmx.min.js", "/static/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      fetch(request, { cache: "no-cache" })
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request).then((hit) => hit || Response.error()))
    );
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (url.pathname === "/week" && response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put("/week", copy));
          }
          return response;
        })
        .catch(() => caches.match("/week").then((hit) => hit || Response.error()))
    );
  }
});
