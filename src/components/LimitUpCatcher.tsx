import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, RefreshCcw, Target } from 'lucide-react'
import { API_BASE } from '../api'
import { cachedJson, setCachedJson } from '../apiCache'

type LimitUpCatcherItem = {
  code: string
  name: string
  volume_ratio: number
  change_pct: number
  turnover_rate: number
  price: number
  intraday_average: number
  average_deviation_pct: number
  source: string
  updated_at: string
}

type LimitUpCatcherResponse = {
  source: string
  updated_at: string
  trade_date?: string | null
  data_status: 'ok' | 'data_gap'
  criteria: {
    volume_ratio_min: number
    change_pct_min: number
    change_pct_max: number
    turnover_rate_min: number
    turnover_rate_max: number
    above_intraday_average: boolean
  }
  items: LimitUpCatcherItem[]
  total_scanned: number
  matched_count: number
  notes: string[]
}

type NumericSortKey =
  | 'volume_ratio'
  | 'change_pct'
  | 'turnover_rate'
  | 'price'
  | 'intraday_average'
  | 'average_deviation_pct'

type SortDirection = 'asc' | 'desc'

const CACHE_KEY = 'limit-up-catcher'
const ENDPOINT = `${API_BASE}/api/market/limit-up-catcher`

const numericColumns: Array<{ key: NumericSortKey; label: string }> = [
  { key: 'volume_ratio', label: '量比' },
  { key: 'change_pct', label: '涨幅' },
  { key: 'turnover_rate', label: '换手率' },
  { key: 'price', label: '现价' },
  { key: 'intraday_average', label: '分时均价' },
  { key: 'average_deviation_pct', label: '偏离均价' },
]

function formatNumber(value: number, digits = 2) {
  return Number.isFinite(value) ? value.toFixed(digits) : '--'
}

function displayTime(value: string) {
  if (!value) return '--'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

export default function LimitUpCatcher() {
  const [data, setData] = useState<LimitUpCatcherResponse | null>(null)
  const [refreshGap, setRefreshGap] = useState<LimitUpCatcherResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sortKey, setSortKey] = useState<NumericSortKey>('volume_ratio')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')

  const load = () => {
    setLoading(true)
    setError('')
    cachedJson<LimitUpCatcherResponse>(CACHE_KEY, ENDPOINT)
      .then(result => {
        setData(result.data)
        setRefreshGap(null)
      })
      .catch(reason => setError(reason instanceof Error ? reason.message : '抓涨停缓存读取失败'))
      .finally(() => setLoading(false))
  }

  const refresh = () => {
    setLoading(true)
    setError('')
    fetch(`${ENDPOINT}/refresh`, { method: 'POST' })
      .then(async response => {
        if (!response.ok) throw new Error(`真实行情刷新失败（${response.status}）`)
        return response.json() as Promise<LimitUpCatcherResponse>
      })
      .then(result => {
        if (result.data_status === 'ok') {
          setCachedJson(CACHE_KEY, result)
          setData(result)
          setRefreshGap(null)
          return
        }
        if (data?.data_status === 'ok') {
          setRefreshGap(result)
          return
        }
        setData(result)
        setRefreshGap(null)
      })
      .catch(reason => setError(reason instanceof Error ? reason.message : '真实行情刷新失败'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const sortedItems = useMemo(() => {
    const multiplier = sortDirection === 'asc' ? 1 : -1
    return [...(data?.items || [])].sort((left, right) => {
      const difference = Number(left[sortKey] || 0) - Number(right[sortKey] || 0)
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

  const criteria = data?.criteria
  const hasDataGap = data?.data_status === 'data_gap'

  return (
    <section className="limit-up-catcher panel">
      <header className="pos-header limit-up-catcher-header">
        <div>
          <span className="eyebrow">盘中异动候选</span>
          <h2><Target size={20} />抓涨停</h2>
          <p>只筛选真实行情中量比、涨幅、换手率和分时均价同时达标的标的；结果是观察线索，不是追涨指令。</p>
        </div>
        <button className="refresh-btn inline" type="button" onClick={refresh} disabled={loading}>
          <RefreshCcw size={14} className={loading ? 'spin' : ''} />
          {loading ? '刷新中' : '刷新真实行情'}
        </button>
      </header>

      {criteria && (
        <div className="limit-up-catcher-criteria" aria-label="抓涨停筛选条件">
          <span>量比 &gt; {formatNumber(criteria.volume_ratio_min, 1)}</span>
          <span>{formatNumber(criteria.change_pct_min, 1)}% &lt; 涨幅 ≤ {formatNumber(criteria.change_pct_max, 1)}%</span>
          <span>{formatNumber(criteria.turnover_rate_min, 1)}% ≤ 换手率 ≤ {formatNumber(criteria.turnover_rate_max, 1)}%</span>
          <span>{criteria.above_intraday_average ? '现价高于分时均价' : '不限分时均价位置'}</span>
        </div>
      )}

      {error && (
        <div className="data-gap-state" role="alert">
          <b>{data?.data_status === 'ok' ? '刷新失败，继续显示上次成功快照' : '抓涨停服务暂不可用'}</b>
          <p>
            {error}。
            {data?.data_status === 'ok'
              ? `当前表格更新时间为 ${displayTime(data.updated_at)}，没有冒充本次刷新数据。`
              : '未取得真实行情时，不能解释为“当前没有候选股”。'}
          </p>
          <button type="button" onClick={load}>重新读取缓存</button>
        </div>
      )}

      {!error && hasDataGap && (
        <div className="data-gap-state" role="status">
          <b>真实行情暂未取到</b>
          <p>{data?.notes?.[0] || '服务端尚无可用快照，请点击“刷新真实行情”。'}不能把数据缺口伪装成“暂无标的”。</p>
        </div>
      )}

      {!error && refreshGap && data?.data_status === 'ok' && (
        <div className="data-gap-state limit-up-refresh-gap" role="alert">
          <b>刷新未取得新行情，继续显示上次成功快照</b>
          <p>{refreshGap.notes?.[0] || '本次真实行情源暂未返回。'}当前表格仍是上次成功结果，更新时间为 {displayTime(data.updated_at)}，没有冒充本次刷新数据。</p>
        </div>
      )}

      {data?.data_status === 'ok' && (
        <>
          <div className="limit-up-catcher-meta">
            <span>行情日 {data.trade_date || '--'}</span>
            <span>扫描 {data.total_scanned.toLocaleString()} 只</span>
            <strong>匹配 {data.matched_count.toLocaleString()} 只</strong>
            <span>来源 {data.source || '--'}</span>
            <span>更新 {displayTime(data.updated_at)}</span>
          </div>

          {sortedItems.length ? (
            <div className="limit-up-catcher-table-wrap">
              <table className="pos-table limit-up-catcher-table">
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>名称</th>
                    {numericColumns.map(column => (
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
                    <th>行情来源</th>
                    <th>行情时点</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedItems.map(item => (
                    <tr key={item.code}>
                      <td>{item.code}</td>
                      <td><b>{item.name}</b></td>
                      <td className="num"><strong>{formatNumber(item.volume_ratio)}</strong></td>
                      <td className={`num ${item.change_pct > 0 ? 'num-up' : ''}`}>{formatNumber(item.change_pct)}%</td>
                      <td className="num">{formatNumber(item.turnover_rate)}%</td>
                      <td className="num">{formatNumber(item.price)}</td>
                      <td className="num">{formatNumber(item.intraday_average)}</td>
                      <td className={`num ${item.average_deviation_pct > 0 ? 'num-up' : ''}`}>{formatNumber(item.average_deviation_pct)}%</td>
                      <td>{item.source || '--'}</td>
                      <td>{displayTime(item.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="limit-up-catcher-empty">
              <b>真实行情已扫描，当前无同时达标标的</b>
              <p>这是筛选结果，不是数据缺口；不要为了凑数量放宽纪律条件。</p>
            </div>
          )}
        </>
      )}

      {loading && !data && !error && <p className="plain-text">正在读取最近一次真实行情快照…</p>}
    </section>
  )
}
