import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import BreakRepackage from './BreakRepackage'
import { setCachedJson } from '../apiCache'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

const response = (payload: unknown) => ({
  ok: true,
  status: 200,
  json: async () => payload,
} as Response)

const criteria = {
  lookback_sessions: 5,
  anchor_source: '东方财富日期涨停池',
  anchor_price_field: '涨停日未复权开盘价',
  require_all_post_anchor_lows_not_below_anchor: true,
  exclude_evaluation_day_limit_up: true,
  near_trigger_pct: 2,
  amount_confirmation_ratio: 1,
  strong_close_position_pct: 65,
}

const item = (
  code: string,
  name: string,
  state: '承接候选' | '临近反包' | '价格反包确认' | '量价反包确认',
  supportMargin: number,
) => ({
  code,
  name,
  state,
  limit_up_date: '2026-07-14',
  sessions_since_limit_up: 3,
  limit_up_open: 10,
  limit_up_close: 11,
  support_low: 10.1,
  support_margin_pct: supportMargin,
  trigger_price: 10.8,
  distance_to_trigger_pct: state.includes('确认') ? -1.2 : 1.2,
  latest_close: 10.93,
  latest_change_pct: 3.2,
  latest_amount_yi: 4.5,
  amount_ratio: 1.25,
  close_position_pct: 80,
  evaluation_date: '2026-07-17',
  daily_evidence: [
    { trade_date: '2026-07-15', low: 10.1, close: 10.4 },
    { trade_date: '2026-07-16', low: 10.2, close: 10.5 },
    { trade_date: '2026-07-17', low: 10.3, close: 10.93 },
  ],
  source: '东方财富日期涨停池+东方财富未复权日线',
  updated_at: '2026-07-20T10:20:00+08:00',
})

const base = {
  source: 'eastmoney-test',
  updated_at: '2026-07-20T10:20:00+08:00',
  evaluation_date: '2026-07-17',
  data_status: 'ok',
  criteria,
  lookback_trade_dates: [
    '2026-07-13',
    '2026-07-14',
    '2026-07-15',
    '2026-07-16',
    '2026-07-17',
  ],
  items: [
    item('600002', '承接测试', '承接候选', 5),
    item('600001', '反包测试', '量价反包确认', 1),
  ],
  candidate_count: 6,
  history_checked_count: 6,
  history_gap_count: 0,
  matched_count: 2,
  notes: [],
}

describe('断板反包', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('初载只读缓存、状态优先且数值可排序，部分缺口不覆盖完整快照', async () => {
    const partial = {
      ...base,
      data_status: 'partial',
      items: [item('600003', '部分结果', '承接候选', 2)],
      history_gap_count: 2,
      matched_count: 1,
      notes: ['另有2只候选因日线缺口未参与结论。'],
    }
    const empty = {
      ...base,
      items: [],
      matched_count: 0,
      notes: ['真实数据核验完成。'],
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(base))
      .mockResolvedValueOnce(response(partial))
      .mockResolvedValueOnce(response(empty))
    vi.stubGlobal('fetch', fetchMock)

    render(<BreakRepackage />)

    expect(await screen.findByText('反包测试')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/market\/break-repackage$/)
    expect(fetchMock.mock.calls[0][1]).toBeUndefined()
    expect(screen.getByText('量价确认：成交额比 ≥ 1.0 倍')).toBeInTheDocument()
    expect(screen.getByText('强势收盘：日内位置 ≥ 65%')).toBeInTheDocument()
    expect(screen.getAllByText('80.00%').length).toBeGreaterThanOrEqual(1)

    let rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('反包测试')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '按支撑余量降序排列' }))
    rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('承接测试')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '按支撑余量升序排列' }))
    rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('反包测试')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '刷新真实数据' }))
    expect(await screen.findByText('本次刷新未完成全部核验，继续显示上次完整快照')).toBeInTheDocument()
    expect(screen.getByText('反包测试')).toBeInTheDocument()
    expect(screen.queryByText('部分结果')).not.toBeInTheDocument()
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST' })

    fireEvent.click(screen.getByRole('button', { name: '刷新真实数据' }))
    expect(await screen.findByText('真实涨停池与日线已核验，当前无满足结构的标的')).toBeInTheDocument()
    expect(screen.queryByText('本次刷新未完成全部核验，继续显示上次完整快照')).not.toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
  })

  test('数据缺口明确区别于真实零候选', async () => {
    const gap = {
      ...base,
      data_status: 'data_gap',
      items: [],
      candidate_count: 0,
      history_checked_count: 0,
      matched_count: 0,
      notes: ['涨停池日期校验失败，不能解释为0候选。'],
    }
    setCachedJson('break-repackage', gap)
    vi.stubGlobal('fetch', vi.fn())

    render(<BreakRepackage />)

    expect(await screen.findByText('数据缺口（不是0候选）')).toBeInTheDocument()
    expect(screen.queryByText('真实涨停池与日线已核验，当前无满足结构的标的')).not.toBeInTheDocument()
    expect(screen.queryByText(/CANDIDATE|CONFIRMED|PARTIAL/)).not.toBeInTheDocument()
  })

  test('部分核验且零匹配时不冒充全量零候选', async () => {
    const partialEmpty = {
      ...base,
      data_status: 'partial',
      items: [],
      history_checked_count: 4,
      history_gap_count: 2,
      matched_count: 0,
      notes: ['另有2只候选缺少完整日线。'],
    }
    setCachedJson('break-repackage', partialEmpty)
    vi.stubGlobal('fetch', vi.fn())

    render(<BreakRepackage />)

    expect(await screen.findByText('已完成核验的部分暂未发现匹配标的')).toBeInTheDocument()
    expect(screen.getByText('仍有 2 只候选缺少完整日线，不能解释为全量0匹配。')).toBeInTheDocument()
    expect(screen.queryByText('这是0匹配，不是数据缺口；涨停日自身最低价不参与锚后支撑检验。')).not.toBeInTheDocument()
  })
})
