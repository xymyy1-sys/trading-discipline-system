import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import MonthlyReview from './MonthlyReview'

describe('建议结果账本', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('展示建议后的客观走势并明确不把采纳率当成功率', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/summary')) {
        return Promise.resolve(ok({
          total: 3,
          status_counts: { complete: 1, partial: 1, pending: 1, invalid: 0 },
          quality_counts: { reliable: 2, degraded: 1 },
          average_returns: { '5m': 9.99 },
        }))
      }
      return Promise.resolve(ok({
        total: 3,
        items: [
          {
            id: 7,
            recommendation_id: 19,
            code: '600584',
            name: '长电科技',
            recommendation_action: '减仓25%',
            reference_at: '2026-07-18T10:00:00+08:00',
            reference_latency_seconds: -120,
            reference_price: 101.11,
            return_5m_pct: 1.2,
            return_15m_pct: -0.5,
            mfe_pct: 2.3,
            mae_pct: -1.1,
            status: 'complete',
            data_quality: 'degraded',
          },
          {
            id: 8,
            code: '600879',
            name: '航天电子',
            status: 'partial',
            data_quality: 'reliable',
            missing_horizons: ['5m', 'close', 'next_open'],
            invalid_reason: '建议时点过晚，尾盘窗口无法形成',
          },
          {
            id: 9,
            code: '600267',
            name: '海正药业',
            status: 'pending',
            data_quality: 'reliable',
            missing_horizons: ['reference', 'next_close'],
          },
        ],
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<MonthlyReview />)

    expect(await screen.findByText('长电科技')).toBeInTheDocument()
    expect(screen.getByText('建议结果账本')).toBeInTheDocument()
    expect(screen.getByText(/建议后的客观价格路径，不是“采纳率”/)).toBeInTheDocument()
    expect(screen.getByText('+1.20%')).toBeInTheDocument()
    expect(screen.getByText('参考快照早于建议 120 秒')).toBeInTheDocument()
    expect(screen.getByText('-0.50%')).toBeInTheDocument()
    expect(screen.getByText('区间最高涨幅 / 区间最低跌幅')).toBeInTheDocument()
    expect(screen.getByText('完整·降级')).toBeInTheDocument()
    expect(screen.getByText('数据质量：降级')).toBeInTheDocument()
    expect(screen.getByText('整体数据质量：可靠 2 · 降级 1')).toBeInTheDocument()
    expect(screen.getAllByText('不适用')).toHaveLength(3)
    expect(screen.getByText('该窗口不适用：5分钟、收盘、次日开盘')).toBeInTheDocument()
    expect(screen.getByText('等待 参考价、次日收盘')).toBeInTheDocument()
    expect(screen.getByText(/区间最高\/最低涨跌未按买入、卖出动作方向判定成败/)).toBeInTheDocument()
    expect(screen.queryByText(/^胜率$/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^成功率$/)).not.toBeInTheDocument()
    expect(screen.queryByText('9.99%')).not.toBeInTheDocument()
  })

  test('明细接口失败但汇总成功时显式报告明细错误', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith('/summary')) return Promise.resolve(ok({ total: 4, status_counts: { pending: 4 } }))
      return Promise.resolve({ ok: false, status: 502, json: async () => ({}) })
    }))
    render(<MonthlyReview />)

    await waitFor(() => expect(screen.getByText('建议明细读取失败')).toBeInTheDocument())
    expect(screen.getByText(/不会把明细缺失伪装成“暂无样本”/)).toBeInTheDocument()
    expect(screen.queryByText('暂无可评估建议')).not.toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  test('接口不可用时不回退到错误的成交金额胜率', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false, json: async () => ({}) })))
    render(<MonthlyReview />)

    await waitFor(() => expect(screen.getByText('无法读取结果账本')).toBeInTheDocument())
    expect(screen.getByText(/不会继续用成交金额伪造胜率/)).toBeInTheDocument()
    expect(screen.queryByText('纪律评分 / 100')).not.toBeInTheDocument()
  })

  test('汇总失败时不把有限明细页伪装成全部样本', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith('/summary')) return Promise.resolve({ ok: false, status: 502, json: async () => ({}) })
      return Promise.resolve(ok([
        { id: 1, code: '600001', name: '样本一', status: 'complete', data_quality: 'reliable' },
        { id: 2, code: '600002', name: '样本二', status: 'pending', data_quality: 'pending' },
      ]))
    }))
    render(<MonthlyReview />)

    await waitFor(() => expect(screen.getByText('汇总读取失败')).toBeInTheDocument())
    expect(screen.getByText(/不会把最多 100 条明细误报成全部样本/)).toBeInTheDocument()
    expect(screen.getByText('汇总不可用，不以当前页代替总量')).toBeInTheDocument()
    expect(screen.getByText('显示 2 条')).toBeInTheDocument()
  })

  test('刷新接口失败时明确保留上一次持久化结果', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') return Promise.resolve({ ok: false, status: 503, json: async () => ({}) })
      if (String(input).endsWith('/summary')) return Promise.resolve(ok({ total: 1, status_counts: { pending: 1 } }))
      return Promise.resolve(ok([{ id: 1, code: '600001', name: '已有结果', status: 'pending', data_quality: 'pending' }]))
    }))
    render(<MonthlyReview />)

    expect(await screen.findByText('已有结果')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /刷新结果/ }))
    await waitFor(() => expect(screen.getByText('刷新未完成')).toBeInTheDocument())
    expect(screen.getByText(/继续展示上一次已持久化的数据/)).toBeInTheDocument()
    expect(screen.getByText('已有结果')).toBeInTheDocument()
  })
})

function ok(payload: unknown) {
  return { ok: true, json: async () => payload }
}
