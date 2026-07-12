import { API_BASE } from './api'

const originalFetch = window.fetch.bind(window)

window.fetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const isApiRequest = url.startsWith(`${API_BASE}/api/`) || (API_BASE === '' && url.startsWith('/api/'))
  return originalFetch(input, isApiRequest ? { ...init, credentials: 'include' } : init).then(response => {
    if (isApiRequest && response.status === 401 && !url.endsWith('/api/auth/session') && !url.endsWith('/api/auth/login')) {
      window.dispatchEvent(new CustomEvent('auth-required'))
    }
    return response
  })
}

export async function getSession(): Promise<boolean> {
  const response = await window.fetch(`${API_BASE}/api/auth/session`)
  return response.ok
}

export async function login(username: string, password: string): Promise<void> {
  const response = await window.fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!response.ok) throw new Error('用户名或密码错误')
}

export async function logout(): Promise<void> {
  await window.fetch(`${API_BASE}/api/auth/logout`, { method: 'POST' })
}
