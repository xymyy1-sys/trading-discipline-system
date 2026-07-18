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
})
