import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { PrivacyModeProvider } from '../privacy'
import type { NextDayPlanOut } from '../types'
import NextDayPlans from './NextDayPlans'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

function jsonResponse(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response
}

const plan: NextDayPlanOut = {
  id: 7,
  plan_date: '2026-07-16',
  plan_type: 'holding',
  holding_id: 3,
  code: '588710',
  name: '科创半导体设备ETF华泰柏瑞',
  quantity: 5300,
  cost_price: 4.05,
  current_price: 3.98,
  market_value: 21094,
  profit_amount: -371,
  profit_ratio: -0.0173,
  price_source: 'realtime',
  price_note: '真实行情',
  position_ratio: 0.237,
  holding_category: '弱于预期',
  classification_basis: {
    sector: '半导体', mainline_position: '跟随', fund_flow: '分歧', amount: '', turnover: '',
    trend: '震荡', support: '3.89', pressure: '4.21', weaker_than_sector: true,
  },
  outperform_condition: '站回均价线', outperform_action: '观察',
  expected_condition: '区间震荡', expected_action: '持有',
  underperform_condition: '跌破支撑', underperform_action: '减仓',
  confirm_price: 4.10,
  trim_price: 4.20,
  trim_condition: '冲高转弱',
  trim_quantity: 1200,
  allow_buyback: false,
  buyback_price: 3.90,
  buyback_condition: '重新站回均价',
  max_buyback_quantity: 800,
  reduce_price: 3.89,
  final_risk_price: 3.80,
  stop_loss_4pct: 3.89,
  limit_up_price: 4.38,
  auction_plan: {
    board_level: '', industry: '半导体', concepts: [], overnight_order: false, order_price: 0,
    limit_up_price: 4.38, keep_order_condition: '', cancel_condition: '', opening_confirmation: '',
    max_position_ratio: 0.15, break_limit_action: '', notes: '', board_strength: '', leader_support: [],
    limit_quality: '', expectation_level: '', strong_boundary_price: 0, weak_reduce_price: 0,
    weak_exit_price: 0, risk_notes: [], intraday_status: '', expected_state: '', expectation_match: '',
    operation_advice: '', volume_price_status: '', board_strength_detail: [], next_day_script: [],
    sell_trigger_cards: [], refreshed_at: '', current_stage: '', stage_decision: '', action_ladder: [], stage_checks: [],
  },
  forbidden_actions: [],
  risk_priority: 2,
  risk_warnings: [],
  review_expectation: '', review_execution: '', review_deviation: '',
  created_at: '2026-07-15T15:10:00+08:00', updated_at: '2026-07-15T15:10:00+08:00',
}

describe('次日计划隐私保护', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('仓位、浮盈、数量、成本、市值、盈亏与计划股数均显示为星号', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/next-day-plans')) return jsonResponse([plan])
      throw new Error(`unexpected request: ${url}`)
    }))

    render(
      <PrivacyModeProvider value>
        <NextDayPlans mode="holding" />
      </PrivacyModeProvider>,
    )

    await waitFor(() => expect(screen.getAllByLabelText('敏感数据已隐藏').length).toBeGreaterThanOrEqual(7))
    expect(screen.queryByText(/23\.7%/)).not.toBeInTheDocument()
    expect(screen.queryByText(/-1\.73%/)).not.toBeInTheDocument()
    expect(screen.queryByText(/5,300 股/)).not.toBeInTheDocument()
    expect(screen.queryByText(/成本 4\.05/)).not.toBeInTheDocument()
    expect(screen.queryByText(/市值 21,094/)).not.toBeInTheDocument()
    expect(screen.queryByText(/盈亏 -371/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('高抛股数')).toHaveValue('******')
    expect(screen.getByLabelText('最大买回股数')).toHaveValue('******')
  })
})
