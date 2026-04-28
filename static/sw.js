const CACHE = 'zomercompetitie-v5';

// Alleen de pure statische bestanden hard cachen
const STATIC_ASSETS = [
  '/static/app.css',
  '/pwa/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-maskable-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // Gooit oude caches weg bij een versie-update
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const isStatic = STATIC_ASSETS.some(asset => event.request.url.includes(asset));

  if (isStatic) {
    // 1. STATISCHE BESTANDEN: Cache First (Lekker snel)
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  } else {
    // 2. DYNAMISCHE PAGINA'S (Dashboard): Network First (Altijd live uitslagen!)
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          // Als we succesvol live data halen, updaten we direct de "offline backup"
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => {
          // Geen internet in de kroeg? Toon dan pas de oude opgeslagen pagina
          return caches.match(event.request);
        })
    );
  }
});
