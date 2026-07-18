// Retirement worker for deployments that previously installed the legacy application cache.
// The application now relies on fingerprinted Vite assets plus HTTP caching;
// an application-shell service worker can otherwise serve an old index that
// references chunks removed by a newer deployment.
self.addEventListener('install', () => self.skipWaiting())

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys()
    await Promise.all(keys.map(key => caches.delete(key)))
    await self.clients.claim()
    await self.registration.unregister()
    const clients = await self.clients.matchAll({ type: 'window' })
    await Promise.all(clients.map(client => client.navigate(client.url)))
  })())
})
