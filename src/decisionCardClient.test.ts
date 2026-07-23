import { afterEach, describe, expect, test, vi } from 'vitest'
import { fetchStockDecisionCard } from './decisionCardClient'
import type { StockDecisionCard } from './types'

vi.mock('./api', () => ({ API_BASE: 'http://localhost:8000' }))

const response = (payload: unknown, ok = true) => ({
  ok,
  status: ok ? 200 : 503,
  json: async () => payload,
} as Response)

const card = (tradeDate: string, current: boolean, latest = current) => ({
  code: '600403',
  market_data_trade_date: tradeDate,
  is_current_session: current,
  is_latest_available: latest,
} as StockDecisionCard)

describe('个股决策卡前端新鲜度保障', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  test('普通快照过期时立即调用刷新端点，盘前也不继续展示两日前缓存', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/decision-card/refresh')) {
        return Promise.resolve(response(card('2026-07-22', false, true)))
      }
      return Promise.resolve(response(card('2026-07-21', false)))
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchStockDecisionCard('600403', { refreshIfStale: true })

    expect(result.market_data_trade_date).toBe('2026-07-22')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/decision-card\/refresh$/)
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'GET', cache: 'no-store' })
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST', cache: 'no-store' })
  })

  test('刷新上游失败时保留已标注过期的只读快照', async () => {
    const stale = card('2026-07-21', false)
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      return Promise.resolve(url.endsWith('/refresh')
        ? response({ detail: '行情源不可用' }, false)
        : response(stale))
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchStockDecisionCard('600403', { refreshIfStale: true })).resolves.toBe(stale)
  })

  test('当前会话数据不重复刷新', async () => {
    const fresh = card('2026-07-23', true)
    const fetchMock = vi.fn(() => Promise.resolve(response(fresh)))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchStockDecisionCard('600403', { refreshIfStale: true })).resolves.toBe(fresh)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
