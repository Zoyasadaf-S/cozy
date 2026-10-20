// Caches the app shell so opening Cozy on your phone costs no network. Bump CACHE on each release.
const CACHE = "cozy-shell-2.0.0";
const SHELL = ["/", "/manifest.webmanifest", "/static/icon-192.png", "/static/icon-512.png"];
self.addEventListener("install", e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;   // API is never cached
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
});
