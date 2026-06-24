// ============================================================
// service-worker.js
// ============================================================
// Offline support for the Nevada Endemics map.
//
// Three caches, three strategies:
//   1. SHELL_CACHE  — app code (HTML/CSS/JS, Leaflet, icons). Cache-first.
//      Bump SHELL_CACHE_VERSION on any deploy that changes these files —
//      activate() deletes the old shell cache automatically.
//   2. DATA_CACHE   — occurrences.geojson / taxa_metadata.json. Network-
//      first, so you get fresh data whenever you're online, falling back
//      to whatever was last cached when you're not.
//   3. TILE_CACHE   — USGS Topo basemap tiles. Cache-first, and any tile
//      fetched fresh over the network gets written into the cache too —
//      so areas you've simply browsed while online become available
//      offline later, on top of whatever was bulk-precached by the page
//      (see precacheTopoTiles() in index.html).
//      NOT bumped on every deploy — only bump if you deliberately change
//      which area/zoom range gets bulk-precached.
//
// Satellite imagery is intentionally NOT cached here (its native zoom
// range makes statewide caching impractical — see chat history for the
// size math). It simply won't render tiles when offline.
// ============================================================

const SHELL_CACHE_VERSION = "v2";
const SHELL_CACHE = `nv-endemics-shell-${SHELL_CACHE_VERSION}`;
const DATA_CACHE = "nv-endemics-data";
const TILE_CACHE = "nv-endemics-topo-tiles-v1"; // keep in sync with index.html

const SHELL_URLS = [
  "./",
  "./index.html",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.css",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.Default.css",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.min.js",
];

self.addEventListener("install", (event) => {
  self.skipWaiting(); // activate the new version on next reload, don't wait for all tabs to close
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_URLS))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== SHELL_CACHE && k !== DATA_CACHE && k !== TILE_CACHE)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = event.request.url;

  // --- USGS Topo tiles: cache-first, opportunistically cache new fetches ---
  if (url.includes("basemap.nationalmap.gov")) {
    event.respondWith(
      caches.open(TILE_CACHE).then((cache) =>
        cache.match(event.request).then((cached) => {
          if (cached) return cached;
          return fetch(event.request)
            .then((resp) => {
              if (resp.ok) cache.put(event.request, resp.clone());
              return resp;
            })
            .catch(() => cached); // offline + not cached -> let it fail naturally (blank tile)
        })
      )
    );
    return;
  }

  // --- Occurrence/taxa data: network-first, cache fallback ---
  if (url.includes("occurrences.geojson") || url.includes("taxa_metadata.json")) {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const clone = resp.clone();
          caches.open(DATA_CACHE).then((cache) => cache.put(event.request, clone));
          return resp;
        })
        .catch(() => caches.open(DATA_CACHE).then((cache) => cache.match(event.request)))
    );
    return;
  }

  // --- Everything else (app shell, Leaflet/markercluster from CDN, and
  //     satellite tiles — which are intentionally never cached, see header
  //     comment): cache-first, fail quietly if offline and not cached ---
  event.respondWith(
    caches.match(event.request).then(
      (cached) =>
        cached ||
        fetch(event.request).catch(
          () => new Response("", { status: 503, statusText: "Offline" })
        )
    )
  );
});
