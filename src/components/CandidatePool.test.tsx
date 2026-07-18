import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import CandidatePool from './CandidatePool'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))
vi.mock('./AiInsightButton', () => ({ default: () => null }))

const response = (payload: unknown) => ({
  ok: true,
  status: 200,
  json: async () => payload,
} as Response)

describe('自动观察池显式刷新', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('首次进入只GET，点击重新分析后直接采用POST的持久化结果', async () => {
    const refreshed = [{
      code: '600001', name: '测试标的', score: 80, tier: '重点观察',
      theme: '测试题材', role: '核心', limit_level: 1, limit_quality: '封板稳定',
      fund_signal: '', expectation_status: '中强预期', volume_price_status: '量价确认',
      expectation_gap: 2, risk_reward_ratio: 2, gate_passed: true,
      missing_conditions: [], reasons: [], risks: [], source: '测试源', category: '昨日涨停承接观察',
      entry_reason: '盘后入选', observation_days: 1, converted: false, updated_at: null,
    }]
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response(refreshed))
    vi.stubGlobal('fetch', fetchMock)

    render(<CandidatePool />)
    const refreshButton = await screen.findByRole('button', { name: /重新分析/ })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/watchlist-recommendations$/)
    expect(fetchMock.mock.calls[0][1]).toBeUndefined()

    fireEvent.click(refreshButton)

    expect(await screen.findByText(/测试标的/)).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/api\/watchlist-recommendations\/refresh$/)
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST' })
  })
})
