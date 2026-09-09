const CACHE = 'jaa-shell-v3';
const PRECACHE = [
  '/static/dashboard/index.html',
  '/static/dashboard/manifest.webmanifest',
  '/static/dashboard/icons/icon-192.png',
  '/static/dashboard/icons/icon-512.png'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(PRECACHE)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  if (u.pathname.startsWith('/api/')) return;

  e.respondWith(
    caches.open(CACHE).then((c) =>
      c.match(e.request).then((hit) =>
        hit ||
        fetch(e.request).then((res) => {
          if (res.ok && res.type === 'basic') c.put(e.request, res.clone());
          return res;
        }).catch(() =>
          c.match('/static/dashboard/index.html').then((shell) => shell || Response.error())
        )
      )
    )
  );
});