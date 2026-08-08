import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import NextDayPlans from './NextDayPlans'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

function response(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response
}

describe('次日计划显式刷新', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('首次进入只 GET，点击刷新现状才 POST 刷新端点', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void init
      const url = String(input)
      if (url.endsWith('/api/next-day-plans')) return response([])
      if (url.endsWith('/api/next-day-plans/refresh')) return response([])
      if (url.endsWith('/api/market/seesaw-monitor')) return response({ holding_alerts: [] })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<NextDayPlans mode="holding" />)

    const refresh = await screen.findByRole('button', { name: /刷新现状/ })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith('/api/next-day-plans'))).toBe(true)
    })
    const initialPlanCall = fetchMock.mock.calls.find(call => String(call[0]).endsWith('/api/next-day-plans'))
    expect(initialPlanCall?.[1]).toBeUndefined()

    fireEvent.click(refresh)

    await waitFor(() => {
      const explicitRefresh = fetchMock.mock.calls.find(call => String(call[0]).endsWith('/api/next-day-plans/refresh'))
      expect(explicitRefresh?.[1]).toMatchObject({ method: 'POST' })
    })
  })

  test('进入打板预案页也不自动生成或改写计划', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/next-day-plans')) return response([])
      if (url.endsWith('/api/market/seesaw-monitor')) return response({ holding_alerts: [] })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<NextDayPlans mode="limit" />)

    await screen.findByRole('button', { name: /刷新现状/ })
    expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith('/api/next-day-plans/generate'))).toBe(false)
  })

  test('逐级晋级账本直接展示每只候选的完整模型指标', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/next-day-plans')) return response([])
      if (url.endsWith('/api/market/seesaw-monitor')) return response({ holding_alerts: [] })
      if (url.endsWith('/api/simulation/ai-trader/promotion-dashboard')) return response({
        model_version: 'promotion-v1', signal_date: '2026-08-07', note: '逐级独立统计',
        history: [{
          from_level: 1, transition: '1进2', sample_count: 0, promoted_count: 0,
          posterior: 33.3, confidence_low: 5, confidence_high: 55, basis: '收缩先验',
        }],
        items: [{
          id: 1, code: '600001', name: '候选甲', theme: '测试题材', from_level: 1,
          target_level: 2, transition: '1进2', probability: 38.3,
          confidence_low: 10, confidence_high: 60, historical_sample_count: 0,
          status: 'PENDING', actual_level: null, same_level_rank: 1,
          same_level_count: 8, trial_position_ratio: 0.15,
        }],
      })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<NextDayPlans mode="limit" />)

    expect(await screen.findByText('逐级晋级样本账本')).toBeInTheDocument()
    expect(await screen.findByText('候选甲')).toBeInTheDocument()
    expect(screen.getAllByText('38.3%').length).toBeGreaterThan(0)
    expect(screen.getByText('10.0%～60.0%')).toBeInTheDocument()
    expect(screen.getByText('1/8')).toBeInTheDocument()
    expect(screen.getByText('15%')).toBeInTheDocument()
  })
})
