const CACHE = 'jaa-shell-v7';
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

// Web push: show a real OS notification even when the dashboard is closed.
self.addEventListener('push', (e) => {
  let data = { title: 'Job Auto-Apply', body: '', url: '/dashboard' };
  try {
    if (e.data) {
      const j = e.data.json();
      data = Object.assign({}, data, j);
    }
  } catch (err) {}

  const options = {
    body: data.body || '',
    icon: '/static/dashboard/icons/icon-192.png',
    badge: '/static/dashboard/icons/icon-192.png',
    tag: 'jaa-' + Date.now(),
    data: { url: data.url || '/dashboard' },
    requireInteraction: true, // stay on screen until clicked/dismissed (no auto-fade in ~5s)
    renotify: false,
    silent: false
  };
  e.waitUntil(self.registration.showNotification(data.title || 'Job Auto-Apply', options));
});

self.addEventListener('notificationclick', (e) => {
  const url = (e.notification.data && e.notification.data.url) || '/dashboard';
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const c of windowClients) {
        const cu = new URL(c.url);
        if (cu.origin === location.origin && 'focus' in c) { c.navigate(url); return c.focus(); }
      }
      return clients.openWindow(url);
    })
  );
});

self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  if (u.pathname.startsWith('/api/')) return;

  const isShell = u.pathname === '/static/dashboard/index.html' || e.request.mode === 'navigate';

  if (isShell) {
    // network-first so the PWA always picks up the newest dashboard instantly
    e.respondWith(
      fetch(e.request).then((res) => {
        if (res.ok) {
          caches.open(CACHE).then((c) => c.put(e.request, res.clone()));
        }
        return res;
      }).catch(() =>
        caches.match('/static/dashboard/index.html').then((shell) => shell || Response.error())
      )
    );
    return;
  }

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