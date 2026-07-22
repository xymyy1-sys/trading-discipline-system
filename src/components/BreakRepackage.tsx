import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, RefreshCcw, RotateCcw } from 'lucide-react'
import { API_BASE } from '../api'
import { cachedJson, setCachedJson } from '../apiCache'

type RepackageState = '承接候选' | '临近反包' | '价格反包确认' | '量价反包确认'

type DailyEvidence = {
  trade_date: string
  low: number
  close: number
}

type BreakRepackageItem = {
  code: string
  name: string
  state: RepackageState
  limit_up_date: string
  sessions_since_limit_up: number
  limit_up_open: number
  limit_up_close: number
  support_low: number
  support_margin_pct: number
  trigger_price?: number | null
  distance_to_trigger_pct?: number | null
  latest_close: number
  latest_change_pct: number
  latest_amount_yi?: number | null
  amount_ratio?: number | null
  close_position_pct?: number | null
  evaluation_date: string
  daily_evidence: DailyEvidence[]
  source: string
  updated_at: string
}

type BreakRepackageResponse = {
  source: string
  updated_at: string
  evaluation_date?: string | null
  data_status: 'ok' | 'partial' | 'data_gap'
  criteria: {
    lookback_sessions: number
    anchor_source: string
    anchor_price_field: string
    require_all_post_anchor_lows_not_below_anchor: boolean
    exclude_evaluation_day_limit_up: boolean
    near_trigger_pct: number
    amount_confirmation_ratio: number
    strong_close_position_pct: number
  }
  lookback_trade_dates: string[]
  items: BreakRepackageItem[]
  candidate_count: number
  history_checked_count: number
  history_gap_count: number
  matched_count: number
  notes: string[]
}

type NumericSortKey =
  | 'sessions_since_limit_up'
  | 'limit_up_open'
  | 'support_low'
  | 'support_margin_pct'
  | 'latest_close'
  | 'latest_change_pct'
  | 'trigger_price'
  | 'distance_to_trigger_pct'
  | 'amount_ratio'
  | 'close_position_pct'

type SortDirection = 'asc' | 'desc'

const CACHE_KEY = 'break-repackage'
const ENDPOINT = `${API_BASE}/api/market/break-repackage`
const statePriority: Record<RepackageState, number> = {
  '量价反包确认': 4,
  '价格反包确认': 3,
  '临近反包': 2,
  '承接候选': 1,
}

const sortableColumns: Array<{ key: NumericSortKey; label: string }> = [
  { key: 'sessions_since_limit_up', label: '断板天数' },
  { key: 'limit_up_open', label: '开盘成本锚' },
  { key: 'support_low', label: '锚后最低价' },
  { key: 'support_margin_pct', label: '支撑余量' },
  { key: 'latest_close', label: '评价日收盘' },
  { key: 'latest_change_pct', label: '评价日涨幅' },
  { key: 'trigger_price', label: '反包触发线' },
  { key: 'distance_to_trigger_pct', label: '距触发线' },
  { key: 'amount_ratio', label: '成交额比' },
  { key: 'close_position_pct', label: '收盘位置' },
]

function formatNumber(value: number | null | undefined, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '--'
}

function formatWithUnit(value: number | null | undefined, unit: string, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(digits)}${unit}`
    : '--'
}

function readableError(reason: unknown, fallback: string) {
  if (!(reason instanceof Error)) return fallback
  return /[\u4e00-\u9fff]/.test(reason.message) ? reason.message : fallback
}

function displayTime(value: string) {
  if (!value) return '--'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

function numericValue(item: BreakRepackageItem, key: NumericSortKey) {
  const value = item[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export default function BreakRepackage() {
  const [data, setData] = useState<BreakRepackageResponse | null>(null)
  const [refreshIssue, setRefreshIssue] = useState<BreakRepackageResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sortKey, setSortKey] = useState<NumericSortKey | null>(null)
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')

  const load = () => {
    setLoading(true)
    setError('')
    cachedJson<BreakRepackageResponse>(CACHE_KEY, ENDPOINT)
      .then(result => {
        setData(result.data)
        setRefreshIssue(null)
      })
      .catch(reason => setError(readableError(reason, '断板反包缓存读取失败')))
      .finally(() => setLoading(false))
  }

  const refresh = () => {
    setLoading(true)
    setError('')
    fetch(`${ENDPOINT}/refresh`, { method: 'POST' })
      .then(async response => {
        if (!response.ok) throw new Error(`真实数据刷新失败（${response.status}）`)
        return response.json() as Promise<BreakRepackageResponse>
      })
      .then(result => {
        if (result.data_status === 'ok') {
          setCachedJson(CACHE_KEY, result)
          setData(result)
          setRefreshIssue(null)
          return
        }
        if (data?.data_status === 'ok') {
          setRefreshIssue(result)
          return
        }
        if (result.data_status === 'partial') {
          setCachedJson(CACHE_KEY, result)
        }
        setData(result)
        setRefreshIssue(null)
      })
      .catch(reason => setError(readableError(reason, '真实数据刷新失败，请检查网络连接')))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const sortedItems = useMemo(() => {
    const items = [...(data?.items || [])]
    if (!sortKey) {
      return items.sort((left, right) => {
        const priority = statePriority[right.state] - statePriority[left.state]
        if (priority) return priority
        const leftDistance = left.distance_to_trigger_pct ?? 999
        const rightDistance = right.distance_to_trigger_pct ?? 999
        return leftDistance === rightDistance
          ? right.support_margin_pct - left.support_margin_pct
          : leftDistance - rightDistance
      })
    }
    const multiplier = sortDirection === 'asc' ? 1 : -1
    return items.sort((left, right) => {
      const leftValue = numericValue(left, sortKey)
      const rightValue = numericValue(right, sortKey)
      if (leftValue === null && rightValue === null) return left.code.localeCompare(right.code)
      if (leftValue === null) return 1
      if (rightValue === null) return -1
      const difference = leftValue - rightValue
      return difference === 0 ? left.code.localeCompare(right.code) : difference * multiplier
    })
  }, [data?.items, sortDirection, sortKey])

  const changeSort = (nextKey: NumericSortKey) => {
    if (nextKey === sortKey) {
      setSortDirection(current => current === 'desc' ? 'asc' : 'desc')
      return
    }
    setSortKey(nextKey)
    setSortDirection('desc')
  }

  const sortIcon = (key: NumericSortKey) => {
    if (sortKey !== key) return <ArrowUpDown size={13} aria-hidden="true" />
    return sortDirection === 'asc'
      ? <ArrowUp size={13} aria-hidden="true" />
      : <ArrowDown size={13} aria-hidden="true" />
  }

  const statusClass = (state: RepackageState) => {
    if (state === '量价反包确认') return 'repackage-state confirmed'
    if (state === '价格反包确认') return 'repackage-state price-confirmed'
    if (state === '临近反包') return 'repackage-state near'
    return 'repackage-state candidate'
  }

  const criteria = data?.criteria
  const currentIssue = data?.data_status === 'data_gap' ? data : null
  const partial = data?.data_status === 'partial' ? data : null

  return (
    <section className="limit-up-catcher break-repackage panel">
      <header className="pos-header limit-up-catcher-header">
        <div>
          <span className="eyebrow">涨停锚点承接筛选</span>
          <h2><RotateCcw size={20} />断板反包</h2>
          <p>寻找近5个完成交易日内曾涨停、评价日未涨停，且涨停后所有最低价都守住涨停日开盘成本锚的股票。</p>
        </div>
        <button className="refresh-btn inline" type="button" onClick={refresh} disabled={loading}>
          <RefreshCcw size={14} className={loading ? 'spin' : ''} />
          {loading ? '核验中' : '刷新真实数据'}
        </button>
      </header>

      {criteria && (
        <div className="limit-up-catcher-criteria" aria-label="断板反包筛选条件">
          <span>最近 {criteria.lookback_sessions} 个完成交易日</span>
          <span>锚点：{criteria.anchor_price_field}</span>
          <span>锚后每日最低价 ≥ 开盘成本锚</span>
          <span>评价日仍涨停则排除</span>
          <span>距触发线 ≤ {formatNumber(criteria.near_trigger_pct, 1)}% 为临近反包</span>
          <span>量价确认：成交额比 ≥ {formatNumber(criteria.amount_confirmation_ratio, 1)} 倍</span>
          <span>强势收盘：日内位置 ≥ {formatNumber(criteria.strong_close_position_pct, 0)}%</span>
        </div>
      )}

      {error && (
        <div className="data-gap-state" role="alert">
          <b>{data?.data_status === 'ok' ? '刷新失败，继续显示上次完整快照' : '断板反包服务暂不可用'}</b>
          <p>{error}。{data?.data_status === 'ok' ? `当前仍显示 ${displayTime(data.updated_at)} 的完整结果。` : '未取得真实数据时，不能解释为“当前0只候选”。'}</p>
          <button type="button" onClick={load}>重新读取缓存</button>
        </div>
      )}

      {!error && currentIssue && (
        <div className="data-gap-state" role="status">
          <b>数据缺口（不是0候选）</b>
          <p>{currentIssue.notes.join('；') || '服务端尚无完整快照，请点击刷新真实数据。'}</p>
        </div>
      )}

      {!error && partial && (
        <div className="data-gap-state repackage-partial" role="status">
          <b>部分候选缺少完整日线，仅展示已核验标的</b>
          <p>{partial.notes.join('；')}</p>
        </div>
      )}

      {!error && refreshIssue && data?.data_status === 'ok' && (
        <div className="data-gap-state limit-up-refresh-gap" role="alert">
          <b>本次刷新未完成全部核验，继续显示上次完整快照</b>
          <p>{refreshIssue.notes.join('；')}当前表格更新时间为 {displayTime(data.updated_at)}，没有冒充本次刷新结果。</p>
        </div>
      )}

      {(data?.data_status === 'ok' || data?.data_status === 'partial') && (
        <>
          <div className="limit-up-catcher-meta">
            <span>评价日 {data.evaluation_date || '--'}</span>
            <span>窗口 {(data.lookback_trade_dates || []).join('、') || '--'}</span>
            <span>非当日涨停候选 {data.candidate_count.toLocaleString()} 只</span>
            <span>完整日线核验 {data.history_checked_count.toLocaleString()} 只</span>
            <strong>结构匹配 {data.matched_count.toLocaleString()} 只</strong>
            <span>更新 {displayTime(data.updated_at)}</span>
          </div>

          {sortedItems.length ? (
            <div className="limit-up-catcher-table-wrap">
              <table className="pos-table limit-up-catcher-table repackage-table">
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>状态</th>
                    <th>最近涨停日</th>
                    {sortableColumns.map(column => (
                      <th className="num" key={column.key}>
                        <button
                          className="limit-up-sort-button"
                          type="button"
                          onClick={() => changeSort(column.key)}
                          aria-label={`按${column.label}${sortKey === column.key && sortDirection === 'desc' ? '升序' : '降序'}排列`}
                        >
                          {column.label}{sortIcon(column.key)}
                        </button>
                      </th>
                    ))}
                    <th>逐日支撑证据</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedItems.map(item => (
                    <tr key={`${item.code}-${item.limit_up_date}`}>
                      <td>{item.code}</td>
                      <td><b>{item.name}</b></td>
                      <td><span className={statusClass(item.state)}>{item.state}</span></td>
                      <td>{item.limit_up_date}</td>
                      <td className="num">{item.sessions_since_limit_up}</td>
                      <td className="num">{formatNumber(item.limit_up_open, 3)}</td>
                      <td className="num">{formatNumber(item.support_low, 3)}</td>
                      <td className="num num-up">+{formatWithUnit(item.support_margin_pct, '%')}</td>
                      <td className="num">{formatNumber(item.latest_close, 3)}</td>
                      <td className={`num ${item.latest_change_pct > 0 ? 'num-up' : item.latest_change_pct < 0 ? 'num-down' : ''}`}>{formatWithUnit(item.latest_change_pct, '%')}</td>
                      <td className="num">{formatNumber(item.trigger_price, 3)}</td>
                      <td className={`num ${(item.distance_to_trigger_pct ?? 1) <= 0 ? 'num-up' : ''}`}>{formatWithUnit(item.distance_to_trigger_pct, '%')}</td>
                      <td className="num">{formatWithUnit(item.amount_ratio, ' 倍')}</td>
                      <td className="num">{formatWithUnit(item.close_position_pct, '%')}</td>
                      <td>
                        <details className="repackage-evidence">
                          <summary>查看 {item.daily_evidence.length} 日</summary>
                          <div>
                            <span>{item.limit_up_date}：涨停收 {formatNumber(item.limit_up_close, 3)}</span>
                            {item.daily_evidence.map(evidence => (
                              <span key={evidence.trade_date}>{evidence.trade_date}：低 {formatNumber(evidence.low, 3)} / 收 {formatNumber(evidence.close, 3)}</span>
                            ))}
                          </div>
                        </details>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : data.data_status === 'partial' ? (
            <div className="limit-up-catcher-empty repackage-partial-empty">
              <b>已完成核验的部分暂未发现匹配标的</b>
              <p>仍有 {data.history_gap_count.toLocaleString()} 只候选缺少完整日线，不能解释为全量0匹配。</p>
            </div>
          ) : (
            <div className="limit-up-catcher-empty">
              <b>真实涨停池与日线已核验，当前无满足结构的标的</b>
              <p>这是0匹配，不是数据缺口；涨停日自身最低价不参与锚后支撑检验。</p>
            </div>
          )}

          <p className="repackage-discipline">候选只表示涨停成本防线仍有效；真正反包还需主线地位、板块订单流、市场环境与突破量价共同确认。</p>
        </>
      )}

      {loading && !data && !error && <p className="plain-text">正在读取最近一次收盘核验快照…</p>}
    </section>
  )
}
