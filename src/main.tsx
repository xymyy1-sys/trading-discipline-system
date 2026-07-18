import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.tsx'
import AuthGate from './components/AuthGate.tsx'

async function retireLegacyApplicationCache() {
  if (!('serviceWorker' in navigator)) return
  try {
    const registrations = await navigator.serviceWorker.getRegistrations()
    const controlled = Boolean(navigator.serviceWorker.controller)
    await Promise.all(registrations.map(registration => registration.unregister()))
    if ('caches' in window) {
      const keys = await window.caches.keys()
      await Promise.all(keys.map(key => window.caches.delete(key)))
    }
    if (controlled && sessionStorage.getItem('legacy-sw-retired') !== 'yes') {
      sessionStorage.setItem('legacy-sw-retired', 'yes')
      window.location.reload()
    }
  } catch {
    // Cache retirement is best-effort and must never block the trading UI.
  }
}

void retireLegacyApplicationCache()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30000, // 30 seconds stale time
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthGate><App /></AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
