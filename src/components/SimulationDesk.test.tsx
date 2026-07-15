import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { SimulationOrdersAndPositions, SimulationPerformanceDesk } from './SimulationDesk'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

const account = {
  id: 7,
  name: '纪律实验账户',
  initial_cash: 1_000_000,
  cash: 900_000,
  commission_rate: 0.0003,
  minimum_commission: 5,
  stamp_tax_rate: 0.0005,
  transfer_fee_rate: 0.00001,
  status: 'ACTIVE',
  created_at: '2026-07-15T09:00:00+08:00',
  updated_at: '2026-07-15T10:31:00+08:00',
}

function jsonResponse(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response
}

describe('模拟盘前端', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('明确标识模拟环境并显示拒单原因', async () => {
    const order = {
      id: 19,
      account_id: 7,
      decision_evidence_snapshot_id: 31,
      strategy_source: 'holding_execution',
      code: '600584',
      name: '长电科技',
      side: 'BUY',
      order_type: 'LIMIT',
      limit_price: 36.5,
      quantity: 100,
      filled_quantity: 0,
      average_fill_price: 0,
      status: 'REJECTED',
      reject_reason: '行情数据已过期，保守拒绝模拟成交',
      client_note: '等待回踩承接确认',
      trade_date: '2026-07-15',
      submitted_at: '2026-07-15T10:30:00+08:00',
      last_evaluated_at: '2026-07-15T10:31:00+08:00',
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/simulation/accounts')) return jsonResponse([account])
      if (url.includes('/positions')) return jsonResponse([])
      if (url.includes('/orders?')) return jsonResponse([order])
      throw new Error(`unexpected request: ${url}`)
    }))

    render(<SimulationOrdersAndPositions />)

    expect(screen.getByText(/不连接券商/)).toBeInTheDocument()
    expect(screen.getByText(/不会真实下单/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/行情数据已过期，保守拒绝模拟成交/)).toBeInTheDocument())
    expect(screen.getByText('已拒绝')).toBeInTheDocument()
  })

  test('绩效接口的百分数不被重复放大并展示三类切片', async () => {
    const slice = {
      key: 'limit_up',
      closed_trade_count: 8,
      sell_count: 8,
      win_count: 5,
      loss_count: 3,
      win_rate: 62.5,
      total_realized_pnl: 18_000,
      average_win: 6_000,
      average_loss: -4_000,
      profit_loss_ratio: 1.5,
    }
    const performance = {
      account_id: 7,
      closed_trade_count: 8,
      sell_count: 8,
      win_count: 5,
      loss_count: 3,
      win_rate: 62.5,
      total_realized_pnl: 18_000,
      profit_loss_ratio: 1.5,
      maximum_drawdown_pct: 12.5,
      by_strategy: [slice],
      by_market_regime: [{ ...slice, key: 'ROTATION' }],
      by_expectation_gap: [{ ...slice, key: 'positive' }],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/simulation/accounts')) return jsonResponse([account])
      if (url.endsWith('/performance')) return jsonResponse(performance)
      throw new Error(`unexpected request: ${url}`)
    }))

    render(<SimulationPerformanceDesk />)

    await waitFor(() => expect(screen.getAllByText('62.50%').length).toBeGreaterThan(0))
    expect(screen.queryByText('6250.00%')).not.toBeInTheDocument()
    expect(screen.getByText('按入场策略分层')).toBeInTheDocument()
    expect(screen.getByText('按入场市场环境分层')).toBeInTheDocument()
    expect(screen.getByText('按入场预期差分层')).toBeInTheDocument()
  })

  test('兼容后端 CANCELED 撤单状态拼写', async () => {
    const canceledOrder = {
      id: 20,
      account_id: 7,
      decision_evidence_snapshot_id: 32,
      strategy_source: 'holding_execution',
      code: '600584',
      name: '长电科技',
      side: 'SELL',
      order_type: 'LIMIT',
      limit_price: 102,
      quantity: 100,
      filled_quantity: 0,
      average_fill_price: 0,
      status: 'CANCELED',
      reject_reason: '用户撤销模拟委托',
      client_note: '',
      trade_date: '2026-07-15',
      submitted_at: '2026-07-15T10:30:00+08:00',
      last_evaluated_at: '2026-07-15T10:31:00+08:00',
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/simulation/accounts')) return jsonResponse([account])
      if (url.includes('/positions')) return jsonResponse([])
      if (url.includes('/orders?')) return jsonResponse([canceledOrder])
      throw new Error(`unexpected request: ${url}`)
    }))

    render(<SimulationOrdersAndPositions />)

    await waitFor(() => expect(screen.getByText('已撤销')).toBeInTheDocument())
  })
})
