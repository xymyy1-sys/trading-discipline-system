import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { PrivacyModeProvider } from '../privacy'
import Positions from './Positions'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

const response = (payload: unknown) => ({ ok: true, status: 200, json: async () => payload, text: async () => '' } as Response)

function holding(id: number, code: string, name: string) {
  return {
    id, code, name, quantity: 100, cost_price: 10, current_price: 11, total_asset: 100000,
    position_type: '观察仓', next_discipline: '按证据执行', market_value: 1100, profit_amount: 100,
    profit_ratio: .1, today_profit_amount: 10, today_profit_ratio: .01, position_ratio: .011,
    stop_loss_price: 9, profit_guard_price: 10.5, price_source: 'realtime', price_note: '实时行情',
    prev_close: 10.9, change_pct: .92, amount: 1000000, turnover: 1, open_price: 10.8,
    high_price: 11.2, low_price: 10.7, sector_flow_status: '', sector_flow_advice: '', updated_at: '2026-07-19T10:30:00+08:00',
  }
}

function execution(id: number, code: string, name: string, state: string, level: string, recommendationId: number | null) {
  return {
    id, holding_id: id, code, name, trade_date: '2026-07-19', state, expectation_state: '符合预期',
    volume_price_state: '量价中性', sector_state: '中性', current_quantity: 100, sellable_quantity: 100,
    today_buy_quantity: 0, yesterday_quantity: 100, current_position_ratio: .1, recommended_position_ratio: .05,
    recommended_action: state === 'EXIT_REQUIRED' ? '全部退出' : state === 'REDUCE_REQUIRED' ? '减仓25%' : '继续观察',
    recommended_reduce_ratio: state === 'EXIT_REQUIRED' ? 1 : .25, structure_stop_price: 9.5, hard_stop_price: 9,
    stop_source: 'next_day_plan', stop_source_detail: '按次日计划执行', trailing_stop_price: 10,
    profit_protection_price: 10.5, t_eligible: false, t_type: 'NO_T', evidence: ['真实量价证据'],
    counter_evidence: ['尚有承接'], invalid_conditions: ['重新跌破结构位'], recovery_conditions: ['重新站回分时均价'],
    events: [], recommendation: recommendationId ? {
      id: recommendationId, trade_date: '2026-07-19', holding_id: id, code, name, level, state,
      action: '按证据执行', recommended_ratio: .5, evidence: ['证据'], counter_evidence: [],
      invalid_conditions: [], recovery_conditions: [], created_at: '2026-07-19T10:30:00+08:00', expires_at: null,
      acknowledged_at: null, feedback_status: '', revision_id: 701, revision_version: 3, decision_hash: 'hash-701',
    } : null,
    profit_snapshot: null, state_history: [], high_sell_signal: null, panic_sell_guard: null,
    contrarian_add_signal: null, data_quality: 'realtime', data_time: '2026-07-19T10:30:00+08:00',
    updated_at: '2026-07-19T10:30:00+08:00',
  }
}

describe('持仓执行建议闭环', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  test('按风险排序展示全部持仓，反馈绑定建议版本且无建议版本时禁用', async () => {
    const holdings = [holding(1, '600001', '高风险持仓'), holding(2, '600002', '中风险持仓'), holding(3, '600003', '普通持仓')]
    const executions = [
      execution(1, '600001', '高风险持仓', 'EXIT_REQUIRED', 'HIGH', 41),
      execution(2, '600002', '中风险持仓', 'REDUCE_REQUIRED', 'MEDIUM', 42),
      execution(3, '600003', '普通持仓', 'HOLD', 'LOW', null),
    ]
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/holdings')) return response(holdings)
      if (url.endsWith('/api/holdings/execution-states')) return response(executions)
      if (url.endsWith('/api/account/asset')) return response({ total_asset: 100000 })
      if (url.endsWith('/api/account/risk')) return response({ trade_date: '2026-07-19', opening_asset: 100000, current_asset: 100100, daily_profit_ratio: .1, level: 'LOW', new_positions_allowed: true, recommended_action: '观察', degraded_position_count: 0, stop_loss_count: 0, data_complete: true, evidence: [], updated_at: '2026-07-19T10:30:00+08:00' })
      if (url.endsWith('/api/holdings/summary')) return response({ today_profit_amount: 10, today_open_profit_amount: 10, today_realized_profit_amount: 0 })
      if (url.endsWith('/api/holdings/portfolio-exposure')) return response({ industries: [], themes: [], risk_factors: [], warnings: [] })
      if (url.endsWith('/api/market/seesaw-monitor')) return response({ source: 'test', updated_at: '', market_mode: '观察', summary: '', inflow_targets: [], outflow_targets: [], holding_alerts: [], notes: [] })
      if (url.endsWith('/api/time-stop-rules')) return response([])
      if (url.includes('/api/t-plans?active_only=true')) return response([])
      if (url.endsWith('/api/recommendations/41/execution-feedback') && init?.method === 'POST') return response({ id: 1 })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'prompt').mockReturnValue('只执行了一半，等待恢复条件')

    const { container } = render(<PrivacyModeProvider value={false}><Positions mode="discipline" /></PrivacyModeProvider>)
    await screen.findAllByText('普通持仓')

    const cards = Array.from(container.querySelectorAll<HTMLElement>('.execution-card'))
    expect(cards).toHaveLength(3)
    expect(cards[0]).toHaveTextContent('高风险持仓')
    expect(cards[0]).toHaveClass('risk-high')
    expect(cards[1]).toHaveTextContent('中风险持仓')
    expect(cards[1]).toHaveClass('risk-medium')
    expect(cards[2]).toHaveTextContent('普通持仓')
    expect(cards[2]).not.toHaveClass('risk-high')
    expect(cards[2]).not.toHaveClass('risk-medium')

    expect(within(cards[2]).getByRole('button', { name: '已执行' })).toBeDisabled()
    fireEvent.click(within(cards[0]).getByRole('button', { name: '部分执行' }))

    await waitFor(() => {
      const feedbackCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/api/recommendations/41/execution-feedback'))
      expect(feedbackCall).toBeTruthy()
      if (!feedbackCall) throw new Error('feedback request missing')
      const body = JSON.parse(String((feedbackCall[1] as RequestInit).body))
      expect(body).toMatchObject({ status: '部分执行', reason: '只执行了一半，等待恢复条件', revision_id: 701 })
      expect(body.client_event_id).toEqual(expect.any(String))
    })
    expect((await screen.findAllByText('当前反馈')).length).toBe(3)
    expect(within(cards[0]).getAllByText('部分执行').length).toBeGreaterThanOrEqual(2)
  })
})
