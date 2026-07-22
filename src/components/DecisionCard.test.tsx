import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import DecisionCard from './DecisionCard'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))
vi.mock('./AiInsightButton', () => ({ default: () => null }))
vi.mock('./PositionAiAssistant', () => ({ default: () => null }))
vi.mock('echarts/core', () => ({ use: vi.fn(), init: vi.fn() }))
vi.mock('echarts/charts', () => ({ BarChart: {}, LineChart: {}, ScatterChart: {} }))
vi.mock('echarts/components', () => ({ GridComponent: {}, LegendComponent: {}, TooltipComponent: {} }))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

const response = (payload: unknown) => ({
  ok: true,
  status: 200,
  json: async () => payload,
} as Response)

const decisionCard = (code: string, name: string) => ({
  code,
  name,
  industry: '测试行业',
  concepts: [],
  current_price: 10,
  change_pct: 1,
  expectation: {
    id: null,
    trade_date: '2026-07-23',
    code,
    name,
    stage: '盘中确认',
    base_expectation: '中性',
    expected_open_low: -1,
    expected_open_high: 1,
    outperform_threshold: 2,
    underperform_threshold: -2,
    severe_underperform_threshold: -4,
    actual_open_pct: 0,
    actual_change_pct: 1,
    expectation_gap_score: 0,
    expectation_result: '符合预期',
    state_transition: '等待验证',
    confidence: 0.8,
    evidence: [],
    counter_evidence: [],
    suggestion: '继续观察',
    created_at: '2026-07-23T10:00:00',
  },
  volume_price: null,
  execution_state: null,
  timeline: [],
  allowed_actions: [],
  forbidden_actions: [],
  t_eligibility: null,
  evidence: [],
  counter_evidence: [],
  data_quality: '真实行情',
  consensus_risk: null,
  minute_chart: [],
  entry_discipline: null,
  effective_capital: null,
  market_data_trade_date: '2026-07-23',
  market_data_as_of: '2026-07-23T10:00:00',
  provider_event_at: '2026-07-23T10:00:00',
  data_age_seconds: 0,
  is_current_session: true,
  data_status_note: '当日数据',
})

describe('个股研判与自动观察池联动', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  test('观察池换届时保留仍在池中的当前标的，移除当前标的后切换到新首项', async () => {
    const names: Record<string, string> = { '600001': '甲标的', '600002': '乙标的', '600003': '丙标的' }
    const initialRows = [
      { code: '600001', name: names['600001'], score: 90, tier: '重点观察' },
      { code: '600002', name: names['600002'], score: 80, tier: '等待确认' },
    ]
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/watchlist-recommendations')) return Promise.resolve(response(initialRows))
      if (url.endsWith('/api/expectation-rules')) return Promise.resolve(response([]))
      if (url.includes('/expectation-chain')) return Promise.resolve(response({ revisions: [] }))
      const match = url.match(/\/api\/stocks\/(\d{6})\/decision-card(?:\/refresh)?$/)
      if (match) return Promise.resolve(response(decisionCard(match[1], names[match[1]])))
      return Promise.reject(new Error(`未处理的测试请求：${url}`))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DecisionCard mode="watchlist" />)
    const firstButton = await screen.findByRole('button', { name: /甲标的/ })
    await waitFor(() => expect(firstButton).toHaveClass('active'))

    await act(async () => {
      window.dispatchEvent(new CustomEvent('watchlist-updated', { detail: {
        rows: [
          { code: '600002', name: names['600002'], score: 82, tier: '等待确认' },
          { code: '600001', name: names['600001'], score: 91, tier: '重点观察' },
        ],
        refreshed: true,
        selectionChanged: true,
      } }))
    })

    await waitFor(() => expect(screen.getByRole('button', { name: /甲标的/ })).toHaveClass('active'))
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/stocks/600001/decision-card/refresh'))).toBe(true)

    await act(async () => {
      window.dispatchEvent(new CustomEvent('watchlist-updated', { detail: {
        rows: [
          { code: '600002', name: names['600002'], score: 83, tier: '等待确认' },
          { code: '600003', name: names['600003'], score: 70, tier: '普通观察' },
        ],
        refreshed: true,
        selectionChanged: true,
      } }))
    })

    await waitFor(() => expect(screen.getByRole('button', { name: /乙标的/ })).toHaveClass('active'))
    expect(screen.queryByRole('button', { name: /甲标的/ })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/stocks/600002/decision-card/refresh'))).toBe(true)
  })

  test('较慢的初始观察池读取不会覆盖后到达的换届事件', async () => {
    let resolveInitial!: (value: Response) => void
    const initialRequest = new Promise<Response>(resolve => { resolveInitial = resolve })
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/watchlist-recommendations')) return initialRequest
      if (url.endsWith('/api/expectation-rules')) return Promise.resolve(response([]))
      if (url.includes('/expectation-chain')) return Promise.resolve(response({ revisions: [] }))
      const match = url.match(/\/api\/stocks\/(\d{6})\/decision-card(?:\/refresh)?$/)
      if (match) return Promise.resolve(response(decisionCard(match[1], match[1] === '600010' ? '新观察标的' : '旧观察标的')))
      return Promise.reject(new Error(`未处理的测试请求：${url}`))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DecisionCard mode="watchlist" />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('watchlist-updated', { detail: {
        rows: [{ code: '600010', name: '新观察标的', score: 92, tier: '重点观察' }],
        refreshed: true,
        selectionChanged: true,
      } }))
    })
    await waitFor(() => expect(screen.getByRole('button', { name: /新观察标的/ })).toHaveClass('active'))

    await act(async () => {
      resolveInitial(response([{ code: '600009', name: '旧观察标的', score: 70, tier: '普通观察' }]))
      await initialRequest
    })

    expect(screen.getByRole('button', { name: /新观察标的/ })).toHaveClass('active')
    expect(screen.queryByRole('button', { name: /旧观察标的/ })).not.toBeInTheDocument()
  })

  test('手工查询进行中收到观察池换届事件时仍以手工查询结果为准', async () => {
    let resolveQuery!: (value: Response) => void
    const queryRequest = new Promise<Response>(resolve => { resolveQuery = resolve })
    const initialRows = [{ code: '600020', name: '池内标的', score: 80, tier: '等待确认' }]
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/watchlist-recommendations')) return Promise.resolve(response(initialRows))
      if (url.endsWith('/api/expectation-rules')) return Promise.resolve(response([]))
      if (url.includes('/expectation-chain')) return Promise.resolve(response({ revisions: [] }))
      if (url.endsWith('/api/stocks/600099/decision-card/refresh')) return queryRequest
      const match = url.match(/\/api\/stocks\/(\d{6})\/decision-card(?:\/refresh)?$/)
      if (match) return Promise.resolve(response(decisionCard(match[1], '池内标的')))
      return Promise.reject(new Error(`未处理的测试请求：${url}`))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DecisionCard mode="watchlist" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /池内标的/ })).toHaveClass('active'))
    fireEvent.change(screen.getByPlaceholderText('输入股票代码'), { target: { value: '600099' } })
    fireEvent.click(screen.getByRole('button', { name: /查询/ }))

    await act(async () => {
      window.dispatchEvent(new CustomEvent('watchlist-updated', { detail: {
        rows: [{ code: '600021', name: '换届首项', score: 90, tier: '重点观察' }],
        refreshed: true,
        selectionChanged: true,
      } }))
    })
    await act(async () => {
      resolveQuery(response(decisionCard('600099', '手工查询标的')))
      await queryRequest
    })

    expect(await screen.findByText('手工查询标的')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/api/stocks/600021/decision-card'))).toBe(false)
  })
})
