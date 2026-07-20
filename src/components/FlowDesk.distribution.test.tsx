import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import FlowDesk from './FlowDesk'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))
vi.mock('../apiCache', () => ({
  cachedJson: async (_key: string, url: string) => {
    const response = await fetch(url)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return { data: await response.json(), fetchedAt: '2026-07-20T10:16:00+08:00' }
  },
}))

const response = (payload: unknown) => ({
  ok: true,
  status: 200,
  json: async () => payload,
} as Response)

const distributionItem = {
  name: '半导体',
  board_code: 'BK1036',
  board_type: '行业',
  heat_score: 88,
  status: '过热兑现风险',
  risk_level: 'HIGH',
  trend_score: 80,
  flow_score: 32,
  crowding_score: 92,
  margin_score: 91,
  attention_score: 90,
  change_pct: -2.4,
  change_pct_5d: 8.2,
  change_pct_10d: 17.6,
  net_inflow: -38.5,
  net_inflow_5d: 12.1,
  net_inflow_10d: 146.2,
  flow_speed: -1.5,
  flow_acceleration: -0.2,
  flow_turning: 'OUTFLOW_ACCELERATING',
  provider_trade_date: '2026-07-20',
  provider_updated_at: '2026-07-20T10:15:00+08:00',
  limit_up_count: 1,
  financing_balance: 1888,
  financing_net_buy: 18,
  financing_balance_ratio: 8.6,
  financing_net_buy_5d: 72,
  financing_net_buy_10d: 155,
  financing_net_buy_20d: 268,
  margin_as_of: '2026-07-18',
  margin_realtime: false,
  distribution_state: '资金承载与杠杆背离确认',
  distribution_risk_level: 'HIGH',
  distribution_risk_score: 86,
  order_flow_exhausted: true,
  leverage_crowding: true,
  price_response_weak: true,
  distribution_confirmation_count: 3,
  distribution_evidence: ['新增订单流转负，价格同时放量下跌。'],
  distribution_counter_evidence: ['近10日仍保有正向订单流，尚不能断言趋势结束。'],
  distribution_actions: ['禁止追高和新增杠杆，等待价格重新响应订单流。'],
  evidence: ['旧通用证据不应覆盖背离专项证据。'],
  counter_evidence: ['旧通用反证。'],
  actions: ['旧通用动作。'],
  data_quality: 'high',
}

describe('冷热拥挤的资金承载与杠杆背离', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('醒目展示联合风险、三项确认、专项证据和T+1边界，但不生成机械卖出含义', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/market/sector-temperature')) {
        return Promise.resolve(response({
          source: '东方财富板块订单流算法+东方财富两融T+1',
          updated_at: '2026-07-20T10:16:00+08:00',
          board_type: '行业',
          lookback_windows: [1, 5, 10, 20],
          items: [distributionItem],
          overheated: [distributionItem],
          stabilizing: [],
          oversold_watch: [],
          notes: ['融资是T+1慢变量，不能单独触发交易。'],
        }))
      }
      return Promise.resolve(response({
        source: 'eastmoney-fflow',
        updated_at: '2026-07-20T10:16:00+08:00',
        board_type: '行业',
        period: '今日',
        inflow: [],
        outflow: [],
        notes: [],
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FlowDesk />)
    fireEvent.click(screen.getByRole('button', { name: /冷热拥挤/ }))

    const panel = await screen.findByLabelText('资金承载与杠杆背离')
    expect(within(panel).getByText(/不等于顶部确认，更不是机械卖出指令/)).toBeInTheDocument()
    const cardTitle = within(panel).getByText('半导体')
    const card = cardTitle.closest('.distribution-divergence-card') as HTMLElement
    expect(card).not.toBeNull()
    expect(card).toHaveClass('risk-high')
    expect(within(card).getByText('高风险')).toBeInTheDocument()
    expect(within(card).getByText('3项证据')).toBeInTheDocument()
    expect(within(card).getByText('订单流衰竭')).toBeInTheDocument()
    expect(within(card).getByText('杠杆拥挤')).toBeInTheDocument()
    expect(within(card).getByText('价格响应转弱')).toBeInTheDocument()
    expect(within(card).getByText(/两融：2026-07-18 · T\+1慢变量/)).toBeInTheDocument()

    fireEvent.click(within(card).getByText('查看依据、反证与纪律动作'))
    expect(within(card).getByText('新增订单流转负，价格同时放量下跌。', { exact: false })).toBeInTheDocument()
    expect(within(card).getByText('近10日仍保有正向订单流，尚不能断言趋势结束。', { exact: false })).toBeInTheDocument()
    expect(within(card).getAllByText('禁止追高和新增杠杆，等待价格重新响应订单流。', { exact: false }).length).toBeGreaterThan(0)
    expect(within(card).queryByText('旧通用证据不应覆盖背离专项证据。', { exact: false })).not.toBeInTheDocument()
    expect(within(card).getByText(/实际减仓仍须个股预期证伪/)).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  })
})
