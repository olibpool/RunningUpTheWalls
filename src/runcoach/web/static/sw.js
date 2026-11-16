// Coachal service worker: static assets cache-first; pages network-first so plan data is
// never stale, falling back to the last week page seen when the network is down.
const CACHE = "coachal-v5";
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
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
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
