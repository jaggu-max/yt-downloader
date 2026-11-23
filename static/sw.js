const CACHE_VERSION = 'tubegrab-v1';
const APP_SHELL = [
  '/',
  '/offline.html',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-192.png',
  '/icons/icon-maskable-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key.startsWith('tubegrab-') && key !== CACHE_VERSION)
        .map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  const firstParty = url.origin === self.location.origin;
  const approvedAssetHosts = ['cdn.tailwindcss.com', 'fonts.googleapis.com', 'fonts.gstatic.com'];
  if (request.method !== 'GET' || (!firstParty && !approvedAssetHosts.includes(url.hostname)) ||
      (firstParty && url.pathname.startsWith('/api/'))) return;

  if (firstParty && request.mode === 'navigate') {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          if (response.ok) {
            await (await caches.open(CACHE_VERSION)).put('/', response.clone());
          }
          return response;
        } catch {
          return (await caches.match(request)) ||
            (await caches.match('/')) || (await caches.match('/offline.html'));
        }
      })()
    );
    return;
  }

  event.respondWith(
    (async () => {
      const cached = await caches.match(request);
      try {
        const response = await fetch(request);
        if (response.ok || response.type === 'opaque') {
          await (await caches.open(CACHE_VERSION)).put(request, response.clone());
        }
        return cached || response;
      } catch (error) {
        if (cached) return cached;
        throw error;
      }
    })()
  );
});
