import { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import CandidatePool from './CandidatePool'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))
vi.mock('./AiInsightButton', () => ({ default: () => null }))

const response = (payload: unknown) => ({
  ok: true,
  status: 200,
  json: async () => payload,
} as Response)

const errorResponse = (status: number, detail: string) => ({
  ok: false,
  status,
  json: async () => ({ detail }),
} as Response)

const recommendation = (code: string, name: string, updatedAt = '2026-07-23T15:05:00') => ({
  code,
  name,
  score: 80,
  tier: '重点观察',
  theme: '测试题材',
  role: '核心',
  limit_level: 1,
  limit_quality: '封板稳定',
  fund_signal: '',
  expectation_status: '中强预期',
  volume_price_status: '量价确认',
  expectation_gap: 2,
  risk_reward_ratio: 2,
  gate_passed: true,
  missing_conditions: [],
  reasons: [],
  risks: [],
  source: '测试源',
  category: '昨日涨停承接观察',
  entry_reason: '盘后入选',
  observation_days: 1,
  converted: false,
  updated_at: updatedAt,
})

const calibration = () => ({
  source_snapshot_date: '',
  outcome_trade_date: '',
  total_count: 0,
  hit_count: 0,
  hit_rate: 0,
  status: 'missing',
  summary: '等待收盘任务或手动刷新后生成校准结果。',
  evidence: [],
  diagnosis: [],
  adjustments: [],
  items: [],
  updated_at: null,
})

const isRecommendationEndpoint = (url: unknown) => {
  const value = String(url)
  return value.includes('/api/watchlist-recommendations') && !value.includes('/calibration')
}

const watchlistEvents = (calls: [event: Event][]) => calls
  .map(([event]) => event)
  .filter((event): event is CustomEvent => event instanceof CustomEvent && event.type === 'watchlist-updated')

describe('自动观察池显式刷新', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  test('首次进入只GET，点击重新分析后直接采用POST的持久化结果', async () => {
    const refreshed = [recommendation('600001', '测试标的')]
    const fetchMock = vi.fn((url: unknown, _init?: RequestInit) => {
      const value = String(url)
      if (value.includes('/calibration')) return Promise.resolve(response(calibration()))
      if (value.endsWith('/api/watchlist-recommendations/refresh')) return Promise.resolve(response(refreshed))
      return Promise.resolve(response([]))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<CandidatePool />)
    const refreshButton = await screen.findByRole('button', { name: /重新分析/ })

    const firstRecommendationCalls = fetchMock.mock.calls.filter(([url]) => isRecommendationEndpoint(url))
    expect(firstRecommendationCalls).toHaveLength(1)
    expect(String(firstRecommendationCalls[0][0])).toMatch(/\/api\/watchlist-recommendations$/)
    expect(firstRecommendationCalls[0][1]).toMatchObject({ cache: 'no-store' })

    fireEvent.click(refreshButton)

    expect(await screen.findByText(/测试标的/)).toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => isRecommendationEndpoint(url))).toHaveLength(2))
    const recommendationCalls = fetchMock.mock.calls.filter(([url]) => isRecommendationEndpoint(url))
    expect(String(recommendationCalls[1][0])).toMatch(/\/api\/watchlist-recommendations\/refresh$/)
    expect(recommendationCalls[1][1]).toMatchObject({ method: 'POST' })
  })

  test('刷新失败保留旧快照，且不派发刷新成功事件', async () => {
    const oldRows = [recommendation('600002', '旧观察标的', '2026-07-22T15:05:00')]
    const fetchMock = vi.fn((url: unknown) => {
      const value = String(url)
      if (value.includes('/calibration')) return Promise.resolve(response(calibration()))
      if (value.endsWith('/api/watchlist-recommendations/refresh')) return Promise.resolve(errorResponse(503, '收盘行情源暂不可用'))
      return Promise.resolve(response(oldRows))
    })
    vi.stubGlobal('fetch', fetchMock)
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    render(<CandidatePool />)
    expect(await screen.findByText(/旧观察标的/)).toBeInTheDocument()
    await waitFor(() => expect(watchlistEvents(dispatchSpy.mock.calls)).toHaveLength(1))
    expect(watchlistEvents(dispatchSpy.mock.calls)[0].detail).toMatchObject({ refreshed: false })
    dispatchSpy.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /重新分析/ }))

    expect(await screen.findByText(/收盘行情源暂不可用/)).toBeInTheDocument()
    expect(screen.getByText(/以下保留2026-07-22成功快照/)).toBeInTheDocument()
    expect(screen.getByText(/旧观察标的/)).toBeInTheDocument()
    expect(watchlistEvents(dispatchSpy.mock.calls).some(event => event.detail?.refreshed === true)).toBe(false)
  })

  test('较慢的GET响应不能覆盖后发POST刷新结果', async () => {
    let resolveSlowGet!: (value: Response) => void
    const slowGet = new Promise<Response>(resolve => { resolveSlowGet = resolve })
    const oldRows = [recommendation('600003', '过期观察标的', '2026-07-22T15:05:00')]
    const refreshedRows = [recommendation('600004', '当日观察标的')]
    let recommendationGetCount = 0
    const fetchMock = vi.fn((url: unknown) => {
      const value = String(url)
      if (value.includes('/calibration')) return Promise.resolve(response(calibration()))
      if (value.endsWith('/api/watchlist-recommendations/refresh')) return Promise.resolve(response(refreshedRows))
      recommendationGetCount += 1
      return recommendationGetCount === 1 ? slowGet : Promise.resolve(response([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    // StrictMode 的重复副作用刻意制造两个GET交错：首个GET仍在途，第二个GET先完成。
    render(<StrictMode><CandidatePool /></StrictMode>)
    const refreshButton = await screen.findByRole('button', { name: /重新分析/ })
    expect(fetchMock.mock.calls.filter(([url]) => isRecommendationEndpoint(url))).toHaveLength(2)
    fireEvent.click(refreshButton)

    expect(await screen.findByText(/当日观察标的/)).toBeInTheDocument()
    expect(watchlistEvents(dispatchSpy.mock.calls).some(event => event.detail?.refreshed === true)).toBe(true)
    dispatchSpy.mockClear()

    await act(async () => {
      resolveSlowGet(response(oldRows))
      await slowGet
    })

    await waitFor(() => {
      expect(screen.getByText(/当日观察标的/)).toBeInTheDocument()
      expect(screen.queryByText(/过期观察标的/)).not.toBeInTheDocument()
    })
    expect(watchlistEvents(dispatchSpy.mock.calls)).toHaveLength(0)
  })
})
