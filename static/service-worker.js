self.addEventListener("install", event => {
  self.skipWaiting();
});

self.addEventListener("fetch", event => {
  // Basic fetch passthrough
  event.respondWith(fetch(event.request));
});
