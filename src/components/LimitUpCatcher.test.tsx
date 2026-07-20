import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import LimitUpCatcher from './LimitUpCatcher'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

const response = (payload: unknown) => ({
  ok: true,
  status: 200,
  json: async () => payload,
} as Response)

const criteria = {
  volume_ratio_min: 3,
  change_pct_min: 0,
  change_pct_max: 5,
  turnover_rate_min: 3,
  turnover_rate_max: 8,
  above_intraday_average: true,
}

const item = (code: string, name: string, volumeRatio: number) => ({
  code,
  name,
  volume_ratio: volumeRatio,
  change_pct: 3.2,
  turnover_rate: 4.6,
  price: 12.5,
  intraday_average: 12.1,
  average_deviation_pct: 3.31,
  source: '东方财富实时行情',
  updated_at: '2026-07-20T10:20:00+08:00',
})

describe('抓涨停', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('初载只GET缓存、数值列可排序，显式刷新使用POST并区分数据缺口与零匹配', async () => {
    const initial = {
      source: 'eastmoney-push2',
      updated_at: '2026-07-20T10:20:00+08:00',
      trade_date: '2026-07-20',
      data_status: 'ok',
      criteria,
      items: [item('600002', '测试乙', 5), item('600001', '测试甲', 3.5)],
      total_scanned: 5879,
      matched_count: 2,
      notes: [],
    }
    const gap = {
      ...initial,
      source: 'cache-unavailable',
      data_status: 'data_gap',
      items: [],
      total_scanned: 0,
      matched_count: 0,
      notes: ['东方财富真实行情暂未返回。'],
    }
    const empty = {
      ...initial,
      items: [],
      matched_count: 0,
      notes: ['真实行情筛选完成。'],
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(gap))
      .mockResolvedValueOnce(response(initial))
      .mockRejectedValueOnce(new Error('网络中断'))
      .mockResolvedValueOnce(response(gap))
      .mockResolvedValueOnce(response(empty))
    vi.stubGlobal('fetch', fetchMock)

    render(<LimitUpCatcher />)

    expect(await screen.findByText('行情供应商采集失败（不是0匹配）')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/market\/limit-up-catcher$/)
    expect(fetchMock.mock.calls[0][1]).toBeUndefined()

    fireEvent.click(screen.getByRole('button', { name: '刷新真实行情' }))
    expect(await screen.findByText('测试乙')).toBeInTheDocument()

    let rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('测试乙')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '按量比升序排列' }))
    rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('测试甲')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '按量比降序排列' }))
    rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('测试乙')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '刷新真实行情' }))
    expect(await screen.findByText('刷新失败，继续显示上次成功快照')).toBeInTheDocument()
    expect(screen.getByText('测试乙')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '刷新真实行情' }))
    expect(await screen.findByText('刷新未完成全A扫描，继续显示上次成功快照')).toBeInTheDocument()
    expect(screen.getByText(/更新时间为/)).toBeInTheDocument()
    expect(screen.getByText('测试乙')).toBeInTheDocument()
    expect(fetchMock.mock.calls[3][1]).toMatchObject({ method: 'POST' })

    fireEvent.click(screen.getByRole('button', { name: '刷新真实行情' }))
    expect(await screen.findByText('真实行情已扫描，当前无同时达标标的')).toBeInTheDocument()
    expect(screen.queryByText('刷新未完成全A扫描，继续显示上次成功快照')).not.toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5))
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST' })
    expect(fetchMock.mock.calls[4][1]).toMatchObject({ method: 'POST' })
  })
})
