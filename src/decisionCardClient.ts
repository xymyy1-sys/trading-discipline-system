import { API_BASE } from './api'
import type { StockDecisionCard } from './types'

type DecisionCardRequestOptions = {
  forceRefresh?: boolean
  refreshIfStale?: boolean
  timeoutMs?: number
}

async function requestDecisionCard(code: string, refresh: boolean, timeoutMs: number) {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(
      `${API_BASE}/api/stocks/${code}/decision-card${refresh ? '/refresh' : ''}`,
      {
        method: refresh ? 'POST' : 'GET',
        cache: 'no-store',
        signal: controller.signal,
      },
    )
    if (!response.ok) {
      const payload = await response.json().catch(() => null) as { detail?: string } | null
      throw new Error(payload?.detail || `个股决策卡读取失败（${response.status}）`)
    }
    return await response.json() as StockDecisionCard
  } finally {
    window.clearTimeout(timeout)
  }
}

/**
 * Read the cached decision card first and, when that snapshot is not from the
 * current session, immediately ask the backend to refresh it. The stale card
 * remains a safe read-only fallback when the upstream quote provider fails.
 *
 * This is intentionally active before 09:15 as well: before the auction the
 * newest valid reference is the previous completed trading day, not an older
 * cache left from two trading days ago.
 */
export async function fetchStockDecisionCard(
  rawCode: string,
  options: DecisionCardRequestOptions = {},
) {
  const code = rawCode.trim()
  const timeoutMs = options.timeoutMs ?? 12_000
  if (!code) throw new Error('股票代码不能为空')

  if (options.forceRefresh) {
    return requestDecisionCard(code, true, timeoutMs)
  }

  const cached = await requestDecisionCard(code, false, timeoutMs)
  if (!options.refreshIfStale || cached.is_latest_available) return cached

  try {
    return await requestDecisionCard(code, true, timeoutMs)
  } catch {
    return cached
  }
}
