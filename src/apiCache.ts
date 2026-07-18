type CacheEntry<T> = {
  expiresAt: number
  fetchedAt: string
  data: T
}

const TTL_MS = 5 * 60 * 1000
const cache = new Map<string, CacheEntry<unknown>>()

export function setCachedJson<T>(key: string, data: T): CacheEntry<T> {
  const entry: CacheEntry<T> = {
    expiresAt: Date.now() + TTL_MS,
    fetchedAt: new Date().toISOString(),
    data,
  }
  cache.set(key, entry)
  return entry
}

export async function cachedJson<T>(key: string, url: string, force = false): Promise<CacheEntry<T>> {
  const now = Date.now()
  const cached = cache.get(key) as CacheEntry<T> | undefined
  if (!force && cached && cached.expiresAt > now) {
    return cached
  }

  const response = await fetch(url)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return setCachedJson<T>(key, await response.json())
}
