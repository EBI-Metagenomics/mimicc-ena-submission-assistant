// ---------------------------------------------------------------------------
// Service worker: serves DataHarmonizer grid schemas the user selected.
//
// Selecting a schema for a grid compiles it in the browser's Python and puts
// the result in Cache Storage ("dh-templates") under the path DataHarmonizer
// fetches it from: /templates/<folder>/schema.json (+ schema.yaml, which the
// study Prepare step reads). This worker answers those requests from the cache
// and falls through to the network — the bundle's built default — for a grid
// never customised. Cache Storage, not memory: an idle service worker is
// killed, and the selection has to survive a browser restart.
//
// Served from /sw.js so its scope covers the page, the /dh/ iframes and the
// Python worker they all fetch through.
// ---------------------------------------------------------------------------
const TEMPLATE_CACHE = "dh-templates";
const TEMPLATE_FILE = /^(?:\/dh)?(\/templates\/[^/]+\/schema\.(?:json|yaml))$/;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const match = event.request.method === "GET" && url.origin === self.location.origin && TEMPLATE_FILE.exec(url.pathname);
  if (!match) return;
  event.respondWith(
    caches.open(TEMPLATE_CACHE)
      .then((cache) => cache.match(match[1]))
      .then((hit) => hit || fetch(event.request)),
  );
});
