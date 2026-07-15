import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { PrivacyModeProvider } from '../privacy'
import Positions from './Positions'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

function jsonResponse(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response
}

const holding = {
  id: 91,
  code: '600001',
  name: '隐私测试持仓',
  quantity: 987654,
  cost_price: 17.89,
  current_price: 45.67,
  total_asset: 123456789,
  position_type: '盈利趋势仓',
  next_discipline: '只按证据执行',
  market_value: 45123456,
  profit_amount: 7654321,
  profit_ratio: 0.2345,
  today_profit_amount: -654321,
  today_profit_ratio: -0.0204,
  position_ratio: 0.4567,
  stop_loss_price: 40.12,
  profit_guard_price: 44.12,
  price_source: 'realtime',
  price_note: '真实行情',
  prev_close: 44.50,
  change_pct: 2.63,
  amount: 100000000,
  turnover: 4.2,
  open_price: 44.80,
  high_price: 46.20,
  low_price: 44.10,
  sector_flow_status: '流入',
  sector_flow_advice: '观察',
  updated_at: '2026-07-15T10:31:00+08:00',
}

const execution = {
  id: 22,
  holding_id: 91,
  code: '600001',
  name: '隐私测试持仓',
  trade_date: '2026-07-15',
  state: 'PROFIT_PROTECTION',
  expectation_state: 'MATCHED',
  volume_price_state: 'VWAP_BROKEN',
  sector_state: 'NEUTRAL',
  current_quantity: 987654,
  sellable_quantity: 87600,
  today_buy_quantity: 11100,
  yesterday_quantity: 976554,
  current_position_ratio: 0.4567,
  recommended_position_ratio: 0.333,
  recommended_action: '等待确认',
  recommended_reduce_ratio: 0.25,
  structure_stop_price: 40.12,
  hard_stop_price: 39.80,
  stop_source: 'next_day_plan',
  stop_source_detail: '当前价 45.67，持仓成本 17.89，计划卖出 32,100 股',
  trailing_stop_price: 43.20,
  profit_protection_price: 44.12,
  t_eligible: true,
  t_type: '顺向T',
  evidence: ['当前价 45.67，持仓盈亏 +7,654,321 元，可卖 87,600 股，成本 17.89'],
  counter_evidence: [],
  invalid_conditions: [],
  recovery_conditions: [],
  events: [{
    id: 1,
    captured_at: '2026-07-15T10:30:00+08:00',
    scope: 'holding',
    target_code: '600001',
    target_name: '隐私测试持仓',
    event_type: 'VWAP_BROKEN',
    severity: 'warning',
    value: 45.67,
    previous_value: 46.02,
    priority: 2,
    group_key: 'test',
    first_seen_at: null,
    last_seen_at: null,
    occurrence_count: 1,
    confirmed: true,
    evidence: ['当前价 45.67 跌破VWAP 46.02，当前盈亏 +7,654,321 元'],
  }],
  recommendation: null,
  profit_snapshot: {
    id: 1,
    holding_id: 91,
    code: '600001',
    captured_at: '2026-07-15T10:30:00+08:00',
    current_profit_pct: 12.34,
    maximum_profit_pct: 18.76,
    profit_drawdown_pct: 9.87,
    maximum_price: 48.00,
    maximum_profit_at: null,
    day_max_profit_pct: 4.3,
    day_max_profit_at: null,
    protection_level: 'PROTECT',
    protection_floor: 44.12,
    triggered: true,
    recommended_action: '保护利润',
  },
  state_history: [{
    id: 1,
    holding_id: 91,
    code: '600001',
    name: '隐私测试持仓',
    trade_date: '2026-07-15',
    old_state: 'HOLD',
    new_state: 'PROFIT_PROTECTION',
    captured_at: '2026-07-15T10:30:00+08:00',
    reason: '当前价 45.67，当前盈亏 +7,654,321 元',
    evidence: [],
  }],
  high_sell_signal: null,
  panic_sell_guard: null,
  contrarian_add_signal: null,
  data_quality: 'REALTIME',
  data_time: '2026-07-15T10:31:00+08:00',
  updated_at: '2026-07-15T10:31:00+08:00',
}

const tPlan = {
  id: 3,
  holding_id: 91,
  trade_date: '2026-07-15',
  code: '600001',
  name: '隐私测试持仓',
  t_type: '顺向T',
  planned_sell_price: 46.20,
  planned_sell_quantity: 32100,
  buyback_price_low: 44.80,
  buyback_price_high: 45.10,
  buyback_conditions: [],
  cancel_conditions: [],
  status: 'partially_bought_back',
  actual_sell_price: 46.10,
  actual_buyback_price: 44.90,
  actual_quantity: 12300,
  actual_sell_quantity: 12300,
  actual_buyback_quantity: 4500,
  execution_note: '',
  cost_reduction: 0,
  evidence: [],
  created_at: '2026-07-15T10:00:00+08:00',
  updated_at: '2026-07-15T10:31:00+08:00',
}

describe('持仓执行隐私模式', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('真实仓位与账户值不进入DOM，但公开行情价格和VWAP仍可见', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/account/asset')) return jsonResponse({ total_asset: 123456789 })
      if (url.endsWith('/api/account/risk')) return jsonResponse({
        trade_date: '2026-07-15',
        opening_asset: 111111111,
        current_asset: 112222222,
        daily_profit_ratio: -2.46,
        level: 'HIGH',
        new_positions_allowed: false,
        recommended_action: '当日亏损 -2.46%，停止扩大仓位',
        degraded_position_count: 0,
        stop_loss_count: 1,
        data_complete: true,
        evidence: ['期初资产 111,111,111 元，当前资产 112,222,222 元'],
        updated_at: '2026-07-15T10:31:00+08:00',
      })
      if (url.includes('/api/market/seesaw-monitor')) return jsonResponse({
        source: 'test', updated_at: '2026-07-15T10:31:00+08:00', market_mode: '轮动', summary: '公开市场轮动',
        inflow_targets: [], outflow_targets: [], holding_alerts: [], notes: [],
      })
      if (url.includes('/api/holdings/execution-states')) return jsonResponse([execution])
      if (url.endsWith('/api/time-stop-rules')) return jsonResponse([])
      if (url.includes('/api/t-plans?active_only=true')) return jsonResponse([tPlan])
      if (url.endsWith('/api/holdings/summary')) return jsonResponse({
        today_profit_amount: -654321,
        today_open_profit_amount: -600000,
        today_realized_profit_amount: -54321,
      })
      if (url.endsWith('/api/holdings/portfolio-exposure')) return jsonResponse({ industries: [], themes: [], risk_factors: [], warnings: [] })
      if (url.endsWith('/api/holdings')) return jsonResponse([holding])
      throw new Error(`unexpected request: ${url}`)
    }))

    const { container } = render(
      <PrivacyModeProvider value>
        <Positions mode="discipline" />
      </PrivacyModeProvider>,
    )

    await screen.findAllByText('隐私测试持仓')
    await waitFor(() => expect(screen.getAllByLabelText('敏感数据已隐藏').length).toBeGreaterThan(10))

    const html = container.innerHTML
    for (const secret of [
      '123456789', '111111111', '112222222', '111,111,111', '112,222,222',
      '987,654', '17.89', '45,123,456', '7,654,321', '-654,321',
      '87,600', '11,100', '32,100', '12,300', '4,500', '18.76', '9.87', '33.3%', '-2.46%',
    ]) expect(html).not.toContain(secret)

    expect(container.textContent).toContain('45.67')
    expect(container.textContent).toContain('VWAP 46.02')
    expect(container.querySelector('td.market-price')).toHaveTextContent('45.67')
    expect(screen.getByLabelText('账户总资产')).toHaveValue('')
    expect(screen.getByLabelText('当日期初资产')).toHaveValue('')
    expect(screen.getAllByLabelText('敏感证据已脱敏').length).toBeGreaterThanOrEqual(3)
  })
})
