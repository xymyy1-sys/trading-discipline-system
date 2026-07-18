import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, AlertCircle, Clock3, DatabaseZap, RefreshCw } from 'lucide-react'
import { API_BASE } from '../api'

type OutcomeRecord = Record<string, unknown> & {
  id?: number
  recommendation_id?: number
  code?: string
  name?: string
  action?: string
  status?: string
  data_quality?: string
}

type OutcomeListResponse = OutcomeRecord[] | {
  items?: OutcomeRecord[]
  outcomes?: OutcomeRecord[]
  total?: number
}

type OutcomeSummary = Record<string, unknown> & {
  total?: number
  complete?: number
  partial?: number
  pending?: number
  invalid?: number
}

type LedgerState = {
  items: OutcomeRecord[]
  listTotal: number | null
  summary: OutcomeSummary | null
}

const EMPTY_LEDGER: LedgerState = { items: [], listTotal: null, summary: null }

export default function MonthlyReview() {
  const [ledger, setLedger] = useState<LedgerState>(EMPTY_LEDGER)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [detailError, setDetailError] = useState('')
  const [summaryError, setSummaryError] = useState('')
  const [refreshError, setRefreshError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError('')
    setDetailError('')
    setSummaryError('')

    const [listResult, summaryResult] = await Promise.allSettled([
      fetchJson<OutcomeListResponse>(`${API_BASE}/api/reviews/recommendation-outcomes?limit=100`, signal),
      fetchJson<OutcomeSummary>(`${API_BASE}/api/reviews/recommendation-outcomes/summary`, signal),
    ])

    if (signal?.aborted) return

    let next = EMPTY_LEDGER
    if (listResult.status === 'fulfilled') {
      const payload = listResult.value
      const items = Array.isArray(payload) ? payload : payload.items ?? payload.outcomes ?? []
      // A bare array is a limited page, not proof of the ledger's total size.
      const listTotal = Array.isArray(payload) ? null : numeric(payload.total)
      next = { ...next, items, listTotal }
    }

    if (summaryResult.status === 'fulfilled') {
      next = { ...next, summary: summaryResult.value }
    }

    if (listResult.status === 'rejected' && isAbortError(listResult.reason)) return
    if (summaryResult.status === 'rejected' && isAbortError(summaryResult.reason)) return

    const listFailed = listResult.status === 'rejected'
    const summaryFailed = summaryResult.status === 'rejected'
    if (listFailed && !summaryFailed) {
      setDetailError('建议明细读取失败；上方只展示汇总进度，不会把明细缺失伪装成“暂无样本”。')
    }
    if (!listFailed && summaryFailed) {
      setSummaryError('汇总读取失败；下方仍展示最近明细，但不会把最多 100 条明细误报成全部样本。')
    }

    if (listFailed && summaryFailed) {
      setError('建议结果账本暂不可用。系统不会继续用成交金额伪造胜率、盈亏比或回撤。')
      setLedger(EMPTY_LEDGER)
    } else {
      setLedger(next)
    }
    setLoading(false)
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    setRefreshError('')
    try {
      const response = await fetch(`${API_BASE}/api/reviews/recommendation-outcomes/refresh`, { method: 'POST' })
      if (!response.ok) throw new Error(`HTTP ${response.status || 'error'}`)
    } catch {
      setRefreshError('本次结果刷新失败，页面继续展示上一次已持久化的数据。')
    } finally {
      setReloadToken(token => token + 1)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load, reloadToken])

  const counts = useMemo(() => deriveCounts(ledger), [ledger])

  return (
    <div className="outcome-ledger-layout">
      <header className="env-hero outcome-ledger-hero">
        <div>
          <span className="eyebrow">前向结果验证</span>
          <h2>建议结果账本</h2>
          <p>记录每条建议发出后 5、15、30 分钟、收盘和下一交易日的真实价格表现，以及采样区间最高涨幅与最低跌幅。</p>
          <p className="outcome-ledger-boundary">这里展示的是建议后的客观价格路径，不是“采纳率”，也不直接等同于策略成功率；区间最高/最低涨跌未按买入、卖出动作方向判定成败。</p>
        </div>
        <button className="refresh-btn" type="button" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw size={16} />{loading ? '同步中' : '刷新结果'}
        </button>
      </header>

      <div className="outcome-ledger-metrics" aria-label="建议结果评估进度">
        <OutcomeMetric icon={<Activity size={18} />} label="已纳入建议" value={formatCount(counts.total)} note={counts.isPageOnly ? '汇总不可用，不以当前页代替总量' : '按建议状态版本记录'} />
        <OutcomeMetric icon={<DatabaseZap size={18} />} label="完整评估" value={formatCount(counts.complete)} note={counts.isPageOnly ? '当前页明细' : '已覆盖规定观察窗口'} tone="complete" />
        <OutcomeMetric icon={<Clock3 size={18} />} label="部分 / 待评估" value={`${formatCount(counts.partial)} / ${formatCount(counts.pending)}`} note={counts.isPageOnly ? '当前页明细' : '等待后续真实行情'} tone="pending" />
        <OutcomeMetric icon={<AlertCircle size={18} />} label="无效样本" value={formatCount(counts.invalid)} note={counts.isPageOnly ? '当前页明细' : '数据缺失或时点不合法'} tone="invalid" />
      </div>
      <p className="outcome-ledger-quality-summary">{qualitySummary(ledger)}</p>

      {error && <div className="panel outcome-ledger-empty"><AlertCircle size={20} /><div><b>无法读取结果账本</b><p>{error}</p></div></div>}
      {!error && detailError && <div className="panel outcome-ledger-empty outcome-ledger-detail-error"><AlertCircle size={20} /><div><b>建议明细读取失败</b><p>{detailError}</p></div></div>}
      {!error && summaryError && <div className="panel outcome-ledger-empty outcome-ledger-detail-error"><AlertCircle size={20} /><div><b>汇总读取失败</b><p>{summaryError}</p></div></div>}
      {!error && refreshError && <div className="panel outcome-ledger-empty outcome-ledger-detail-error"><AlertCircle size={20} /><div><b>刷新未完成</b><p>{refreshError}</p></div></div>}
      {!error && loading && !ledger.items.length && <div className="panel outcome-ledger-empty"><Clock3 size={20} /><div><b>正在同步前向结果</b><p>只读取建议产生之后的真实快照，不使用未来数据回填决策。</p></div></div>}
      {!error && !detailError && !loading && !ledger.items.length && <div className="panel outcome-ledger-empty"><DatabaseZap size={20} /><div><b>暂无可评估建议</b><p>新账本会在建议发出后等待真实行情窗口成熟；样本不足时不展示胜率或参数优劣结论。</p></div></div>}

      {!!ledger.items.length && <section className="panel outcome-ledger-panel">
        <header>
          <div><h3>最近建议的前向走势</h3><p>收益均以建议产生后的可追溯参考价计算；“--”表示该观察窗口尚未成熟或数据不合格。</p></div>
          <span>显示 {ledger.items.length} 条{counts.total > ledger.items.length ? ` / 共 ${counts.total} 条` : ''}</span>
        </header>
        <div className="outcome-ledger-table-wrap">
          <table className="outcome-ledger-table">
            <thead><tr><th>建议</th><th>参考时点 / 价格</th><th>5分钟</th><th>15分钟</th><th>30分钟</th><th>收盘</th><th>次日开盘</th><th>次日收盘</th><th>区间最高涨幅 / 区间最低跌幅</th><th>评估状态</th></tr></thead>
            <tbody>{ledger.items.map((item, index) => <OutcomeRow key={String(item.id ?? item.recommendation_id ?? `${text(item, ['code'])}-${index}`)} item={item} />)}</tbody>
          </table>
        </div>
      </section>}

      <section className="panel outcome-ledger-method">
        <h3>统计边界</h3>
        <div>
          <p><b>不再计算：</b>不能用成交金额正负判断交易输赢，因此旧版“月度胜率、均盈利、盈亏比、累计亏损/回撤”已下线。</p>
          <p><b>可以回答：</b>建议发出后价格实际怎样走、采样区间最高涨幅与最低跌幅是多少、数据是否完整。</p>
          <p><b>不能直接判成败：</b>区间最高/最低涨跌是原始价格路径，未按建议动作方向解释；汇总平均收益也不展示为成功率。</p>
          <p><b>暂不能回答：</b>在缺少真实执行反馈、费用、仓位和完整退出记录前，不宣称某条规则有效，也不自动修改参数。</p>
        </div>
      </section>
    </div>
  )
}

function OutcomeMetric({ icon, label, value, note, tone = '' }: { icon: React.ReactNode; label: string; value: string; note: string; tone?: string }) {
  return <div className={`outcome-ledger-metric ${tone}`}>{icon}<div><span>{label}</span><strong>{value}</strong><small>{note}</small></div></div>
}

function OutcomeRow({ item }: { item: OutcomeRecord }) {
  const code = text(item, ['code', 'stock_code']) || '--'
  const name = text(item, ['name', 'stock_name'])
  const action = text(item, ['action', 'recommendation_action', 'recommended_action']) || '动作待确认'
  const referenceAt = text(item, ['reference_at', 'reference_time', 'signal_at', 'created_at'])
  const referencePrice = numberFrom(item, ['reference_price', 'signal_price'])
  const referenceSource = text(item, ['reference_source'])
  const referenceQuality = text(item, ['reference_quality'])
  const referenceLatency = numberFrom(item, ['reference_latency_seconds'])
  const status = text(item, ['evaluation_status', 'status']) || 'pending'
  const dataQuality = text(item, ['data_quality']) || 'unknown'
  const missingHorizons = stringList(item.missing_horizons)
  const translatedHorizons = missingHorizons.map(horizonLabel)
  const rawReason = text(item, ['invalid_reason', 'pending_reason', 'reason'])
  const structurallyUnavailable = [rawReason, ...missingHorizons].some(value => value.includes('建议时点过晚'))
  const reason = structurallyUnavailable
    ? `该窗口不适用${translatedHorizons.length ? `：${translatedHorizons.join('、')}` : ''}`
    : rawReason || (translatedHorizons.length ? `等待 ${translatedHorizons.join('、')}` : '')

  return <tr className={dataQuality.toLowerCase().includes('degraded') ? 'outcome-row-degraded' : ''}>
    <td><b>{name || code}</b><small>{code} · {action}</small></td>
    <td><span>{formatTime(referenceAt)}</span><small>{formatPrice(referencePrice)}</small>{referenceLatency !== null && <small>{formatReferenceLatency(referenceLatency)}</small>}{(referenceSource || referenceQuality) && <small>{[referenceSource, referenceQuality].filter(Boolean).join(' · ')}</small>}</td>
    <ReturnCell value={numberFrom(item, ['return_5m_pct', 'price_return_5m_pct', 'horizon_5m_return_pct', 'return_5m'])} unavailable={structurallyUnavailable && includesHorizon(missingHorizons, '5m')} />
    <ReturnCell value={numberFrom(item, ['return_15m_pct', 'price_return_15m_pct', 'horizon_15m_return_pct', 'return_15m'])} unavailable={structurallyUnavailable && includesHorizon(missingHorizons, '15m')} />
    <ReturnCell value={numberFrom(item, ['return_30m_pct', 'price_return_30m_pct', 'horizon_30m_return_pct', 'return_30m'])} unavailable={structurallyUnavailable && includesHorizon(missingHorizons, '30m')} />
    <ReturnCell value={numberFrom(item, ['return_close_pct', 'close_return_pct', 'return_at_close_pct'])} unavailable={structurallyUnavailable && includesHorizon(missingHorizons, 'close')} />
    <ReturnCell value={numberFrom(item, ['return_next_open_pct', 'next_open_return_pct'])} unavailable={structurallyUnavailable && includesHorizon(missingHorizons, 'next_open')} />
    <ReturnCell value={numberFrom(item, ['return_next_close_pct', 'next_close_return_pct'])} unavailable={structurallyUnavailable && includesHorizon(missingHorizons, 'next_close')} />
    <td><span className={numberTone(numberFrom(item, ['mfe_pct', 'max_favorable_excursion_pct']))}>{formatReturn(numberFrom(item, ['mfe_pct', 'max_favorable_excursion_pct']))}</span><small className={numberTone(numberFrom(item, ['mae_pct', 'max_adverse_excursion_pct']))}>{formatReturn(numberFrom(item, ['mae_pct', 'max_adverse_excursion_pct']))}</small></td>
    <td><span className={`outcome-status ${statusTone(status, dataQuality)}`}>{statusLabel(status, dataQuality)}</span><small className="outcome-quality">数据质量：{qualityLabel(dataQuality)}</small>{reason && <small title={reason}>{reason}</small>}</td>
  </tr>
}

function ReturnCell({ value, unavailable = false }: { value: number | null; unavailable?: boolean }) {
  return <td><span className={unavailable && value === null ? 'outcome-window-na' : numberTone(value)}>{unavailable && value === null ? '不适用' : formatReturn(value)}</span></td>
}

function deriveCounts(ledger: LedgerState) {
  const statuses = ledger.items.map(item => text(item, ['evaluation_status', 'status', 'data_quality']).toLowerCase())
  const statusCounts = ledger.summary?.status_counts
  const nestedCounts = typeof statusCounts === 'object' && statusCounts !== null ? statusCounts as Record<string, unknown> : {}
  const fromSummary = (keys: string[]) => numberFrom(ledger.summary ?? {}, keys)
  const fromStatusCounts = (keys: string[]) => numberFrom(nestedCounts, keys)
  const count = (patterns: string[]) => statuses.filter(status => patterns.some(pattern => status.includes(pattern))).length
  return {
    total: fromSummary(['total', 'total_count']) ?? ledger.listTotal ?? Number.NaN,
    complete: fromStatusCounts(['complete', 'completed']) ?? fromSummary(['complete', 'completed', 'complete_count', 'completed_count']) ?? count(['complete', 'completed']),
    partial: fromStatusCounts(['partial']) ?? fromSummary(['partial', 'partial_count']) ?? count(['partial']),
    pending: fromStatusCounts(['pending', 'waiting']) ?? fromSummary(['pending', 'pending_count']) ?? count(['pending', 'waiting']),
    invalid: fromStatusCounts(['invalid', 'failed']) ?? fromSummary(['invalid', 'invalid_count']) ?? count(['invalid', 'failed']),
    isPageOnly: ledger.summary === null,
  }
}

function text(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function numberFrom(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = numeric(record[key])
    if (value !== null) return value
  }
  return null
}

function numeric(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value)
  return null
}

function stringList(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())) : []
}

function horizonLabel(value: string) {
  const replacements: Array<[RegExp, string]> = [
    [/\bnext_close\b/gi, '次日收盘'],
    [/\bnext_open\b/gi, '次日开盘'],
    [/\breference\b/gi, '参考价'],
    [/\bclose\b/gi, '收盘'],
    [/\b30m\b/gi, '30分钟'],
    [/\b15m\b/gi, '15分钟'],
    [/\b5m\b/gi, '5分钟'],
  ]
  return replacements.reduce((label, [pattern, replacement]) => label.replace(pattern, replacement), value)
}

function includesHorizon(values: string[], horizon: string) {
  return values.some(value => value.toLowerCase().split(/[\s:：()（）]/).includes(horizon))
}

function formatCount(value: number) {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN') : '--'
}

function formatReturn(value: number | null) {
  return value === null ? '--' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatPrice(value: number | null) {
  return value === null ? '参考价待确认' : `参考价 ${value.toFixed(2)}`
}

function formatReferenceLatency(value: number) {
  if (Math.abs(value) < 0.5) return '参考快照与建议时点同步'
  const seconds = Math.round(Math.abs(value))
  return `参考快照${value > 0 ? '晚于' : '早于'}建议 ${seconds} 秒`
}

function formatTime(value: string) {
  if (!value) return '时点待确认'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

function numberTone(value: number | null) {
  return value === null || value === 0 ? '' : value > 0 ? 'num-up' : 'num-down'
}

function statusTone(status: string, quality: string) {
  const normalized = status.toLowerCase()
  if (normalized.includes('complete') && quality.toLowerCase().includes('degraded')) return 'complete-degraded'
  if (normalized.includes('complete')) return 'complete'
  if (normalized.includes('invalid') || normalized.includes('failed')) return 'invalid'
  if (normalized.includes('partial')) return 'partial'
  return 'pending'
}

function statusLabel(status: string, quality: string) {
  const normalized = status.toLowerCase()
  if (normalized.includes('complete') && quality.toLowerCase().includes('degraded')) return '完整·降级'
  if (normalized.includes('complete')) return '完整'
  if (normalized.includes('invalid') || normalized.includes('failed')) return '无效'
  if (normalized.includes('partial')) return '部分'
  if (normalized.includes('pending') || normalized.includes('waiting')) return '待评估'
  return status
}

function qualityLabel(quality: string) {
  const normalized = quality.toLowerCase()
  if (normalized.includes('degraded')) return '降级'
  if (normalized.includes('reliable') || normalized === 'ok' || normalized.includes('realtime')) return '可靠'
  if (normalized.includes('partial')) return '部分可用'
  if (normalized.includes('missing')) return '缺失'
  if (normalized.includes('invalid')) return '无效'
  if (normalized.includes('manual')) return '手工数据'
  return quality === 'unknown' ? '待确认' : quality
}

function qualitySummary(ledger: LedgerState) {
  const rawCounts = ledger.summary?.quality_counts
  const counts = typeof rawCounts === 'object' && rawCounts !== null ? rawCounts as Record<string, unknown> : null
  if (counts) {
    const entries = Object.entries(counts)
      .map(([quality, value]) => ({ quality: qualityLabel(quality), value: numeric(value) }))
      .filter((entry): entry is { quality: string; value: number } => entry.value !== null && entry.value > 0)
    if (entries.length) return `整体数据质量：${entries.map(entry => `${entry.quality} ${entry.value}`).join(' · ')}`
  }
  if (ledger.items.length) {
    const itemCounts = new Map<string, number>()
    ledger.items.forEach(item => {
      const label = qualityLabel(text(item, ['data_quality']) || 'unknown')
      itemCounts.set(label, (itemCounts.get(label) ?? 0) + 1)
    })
    return `当前明细数据质量：${[...itemCounts.entries()].map(([label, value]) => `${label} ${value}`).join(' · ')}`
  }
  return '整体数据质量：等待可评估样本'
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal })
  if (!response.ok) throw new Error(`HTTP ${response.status || 'error'}`)
  return response.json() as Promise<T>
}

function isAbortError(reason: unknown) {
  return reason instanceof DOMException && reason.name === 'AbortError'
}
