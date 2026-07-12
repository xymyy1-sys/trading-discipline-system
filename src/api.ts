function detectApiBase(): string {
  const env = import.meta.env.VITE_API_BASE
  if (env) return env
  const host = window.location.hostname
  if (host === 'localhost' || host === '127.0.0.1') return 'http://localhost:8000'
  return ''
}

export const API_BASE = detectApiBase()
