import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, CalendarClock, Flame, NotebookPen, RefreshCcw, Search, ShieldAlert, Trophy } from 'lucide-react'
import { API_BASE } from '../api'
import { cachedJson } from '../apiCache'

import type {
  LimitUpStock,
  LimitUpAtmosphere,
  LimitUpLadder as LimitUpLadderData,
} from '../types'

async function refreshJson<T>(url: string): Promise<{ data: T; fetchedAt: string }> {
  const response = await fetch(url, { method: 'POST' })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return { data: await response.json() as T, fetchedAt: new Date().toISOString() }
}

export default function LimitUpLadder() {
  const [data, setData] = useState<LimitUpLadderData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [atmosphere, setAtmosphere] = useState<LimitUpAtmosphere | null>(null)
  const [atmosphereError, setAtmosphereError] = useState('')
  const [creatingCode, setCreatingCode] = useState<string | null>(null)
  const [activeConcept, setActiveConcept] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  const loadData = useCallback((force = false) => {
    setLoading(true)
    setError('')
    setAtmosphereError('')
    const ladderPath = `${API_BASE}/api/market/limit-up-ladder`
    const atmospherePath = `${API_BASE}/api/market/limit-up-atmosphere`
    Promise.allSettled([
      force
        ? refreshJson<LimitUpLadderData>(`${ladderPath}/refresh`)
        : cachedJson<LimitUpLadderData>('limit-up-ladder', ladderPath),
      force
        ? refreshJson<LimitUpAtmosphere>(`${atmospherePath}/refresh`)
        : cachedJson<LimitUpAtmosphere>('limit-up-atmosphere', atmospherePath),
    ])
      .then(([ladderResult, atmosphereResult]) => {
        if (ladderResult.status === 'fulfilled') setData(ladderResult.value.data)
        else setError('涨停天梯暂不可用，请确认后端服务和行情源')
        if (atmosphereResult.status === 'fulfilled') setAtmosphere(atmosphereResult.value.data)
        else setAtmosphereError('打板氛围暂不可用；数据恢复前不生成打板许可')
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  const filteredGroups = useMemo(() => {
    const keyword = query.trim()
    return (data?.groups ?? []).map(group => ({
      ...group,
      stocks: group.stocks.filter(stock => {
        const hitConcept = !activeConcept || stock.concepts.includes(activeConcept) || stock.industry === activeConcept
        const hitQuery = !keyword || `${stock.code}${stock.name}${stock.industry}${stock.concepts.join('')}`.includes(keyword)
        return hitConcept && hitQuery
      }),
    })).filter(group => group.stocks.length)
  }, [activeConcept, data, query])

  const stockStrategyByCode = useMemo(() => {
    const mapping = new Map<string, { theme: LimitUpAtmosphere['theme_ladders'][number]; role: LimitUpAtmosphere['theme_ladders'][number]['identity_roles'][number] }>()
    for (const theme of atmosphere?.theme_ladders ?? []) {
      for (const role of theme.identity_roles) {
        const previous = mapping.get(role.code)
        const priority = (theme.is_mainline ? 1000 : 0) + theme.completeness_score + role.role_score + role.max_position_ratio * 100
        const previousPriority = previous
          ? (previous.theme.is_mainline ? 1000 : 0) + previous.theme.completeness_score + previous.role.role_score + previous.role.max_position_ratio * 100
          : -1
        if (role.code && priority > previousPriority) mapping.set(role.code, { theme, role })
      }
    }
    return mapping
  }, [atmosphere])

  const highest = data?.groups[0]?.level ?? 0
  const total = data?.groups.reduce((sum, group) => sum + group.stocks.length, 0) ?? 0
  const topCluster = data?.clusters[0]
  const isRecentTradeDay = !!data?.notes.some(note => note.includes('非交易日') || note.includes('最近交易日'))

  const createAuctionPlan = (stock: LimitUpStock, level: number) => {
    const strategy = stockStrategyByCode.get(stock.code)
    setCreatingCode(stock.code)
    fetch(`${API_BASE}/api/next-day-plans/from-limit-up`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...stock, level, max_position_ratio: strategy?.role.max_position_ratio ?? 0 }),
    })
      .then(r => r.json())
      .then(() => {
        window.dispatchEvent(new CustomEvent('workspace-module', { detail: 'plans' }))
      })
      .finally(() => setCreatingCode(null))
  }

  return (
    <section className="ladder-page trading-desk-page">
      <div className="ladder-command">
        <div className="desk-heading">
          <span className="eyebrow">涨停质量天梯</span>
          <h2>涨停天梯</h2>
          <p>按连板高度、封单质量、题材聚类观察市场情绪，优先服务次日打板预案。</p>
        </div>
        <div className="ladder-actions">
          <button className="refresh-btn inline" type="button" onClick={() => loadData(true)} disabled={loading}>
            <RefreshCcw size={14} />
            {loading ? '同步中' : '刷新'}
          </button>
          <span className={`trade-day-pill ${isRecentTradeDay ? 'stale' : ''}`}>
            <CalendarClock size={14} />
            {data ? `${data.trade_date} · ${sourceLabel(data.source)}` : error || '等待同步'}
          </span>
        </div>
      </div>

      <AtmospherePanel data={atmosphere} loading={loading} error={atmosphereError} onSelectTheme={setActiveConcept} />

      <div className="ladder-summary">
        <SummaryCard label="涨停家数" value={`${total}只`} icon={<Flame size={17} />} />
        <SummaryCard label="最高连板" value={highest ? `${highest}板` : '--'} icon={<Trophy size={17} />} />
        <SummaryCard label="最强聚类" value={topCluster ? topCluster.name : '--'} />
        <SummaryCard label="题材聚类" value={`${data?.clusters.length ?? 0}条`} />
      </div>

      <div className="ladder-layout">
        <aside className="panel ladder-clusters">
          <div className="panel-title-line">
            <h3>题材聚类</h3>
            <span>{activeConcept ?? '全部'}</span>
          </div>
          <button
            className={`concept-row ${activeConcept === null ? 'active' : ''}`}
            type="button"
            onClick={() => setActiveConcept(null)}
          >
            <strong>全部涨停</strong>
            <span>{total}只</span>
          </button>
          {(data?.clusters ?? []).slice(0, 18).map(cluster => (
            <button
              className={`concept-row ${activeConcept === cluster.name ? 'active' : ''}`}
              key={cluster.name}
              type="button"
              onClick={() => setActiveConcept(cluster.name)}
            >
              <strong>{cluster.name}</strong>
              <span>{cluster.count}只 / 最高{cluster.highest_level}板</span>
              <small>{cluster.expectation}</small>
            </button>
          ))}
        </aside>

        <div className="ladder-main">
          <div className="ladder-toolbar panel">
            <label className="search-box">
              <Search size={15} />
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索股票 / 板块 / 概念" />
            </label>
            <span>{loading ? '同步中' : `${filteredGroups.reduce((sum, group) => sum + group.stocks.length, 0)} 只匹配`}</span>
          </div>

          <div className="ladder-groups">
            {filteredGroups.map(group => (
              <section className="ladder-group" key={group.level}>
                <div className="ladder-level">
                  <strong>{group.label}</strong>
                  <span>{group.stocks.length}只</span>
                </div>
                <div className="limit-stock-grid">
                  {group.stocks.map(stock => (
                    <article className="limit-stock-card" key={stock.code || stock.name}>
                      <div className="limit-card-head">
                        <div>
                          <strong>{stock.name}</strong>
                          <span className="mono">{stock.code}</span>
                        </div>
                        <b>{stock.consecutive_limit_days}板</b>
                      </div>
                      {stockStrategyByCode.get(stock.code) ? (() => {
                        const strategy = stockStrategyByCode.get(stock.code)!
                        return (
                          <div className={`stock-mainline-context risk-${strategy.role.risk_level}`}>
                            <div>
                              <span>{strategy.theme.mainline_level}</span>
                              <span>{strategy.theme.stage}阶段</span>
                              <span>{strategy.role.roles.join(' / ')}</span>
                            </div>
                            <p>{strategy.role.recommended_action}</p>
                            <b>打板仓位上限 {Math.round(strategy.role.max_position_ratio * 100)}%</b>
                          </div>
                        )
                      })() : (
                        <div className="stock-mainline-context risk-高">
                          <p>未取得主线、题材阶段和前排身份的联合证据，只允许观察。</p>
                          <b>打板仓位上限 0%</b>
                        </div>
                      )}
                      <div className="limit-card-stats">
                        <span>涨幅 <em>{stock.change_pct.toFixed(2)}%</em></span>
                        <span>封单 <em>{stock.sealed_amount.toFixed(2)}亿</em></span>
                        <span>成交 <em>{stock.amount.toFixed(2)}亿</em></span>
                        <span>炸板 <em>{stock.break_count}次</em></span>
                      </div>
                      <div className="limit-card-tags">
                        {[stock.industry, ...stock.concepts].filter(Boolean).slice(0, 4).map(tag => (
                          <button type="button" key={tag} onClick={() => setActiveConcept(tag)}>{tag}</button>
                        ))}
                      </div>
                      <p>{stock.expectation}</p>
                      <small>{stock.first_limit_time || '--'} 首封 / {stock.last_limit_time || '--'} 末封</small>
                      <button
                        className="limit-plan-btn"
                        type="button"
                        onClick={() => createAuctionPlan(stock, group.level)}
                        disabled={creatingCode === stock.code}
                      >
                        <NotebookPen size={14} />
                        {creatingCode === stock.code ? '生成中' : (stockStrategyByCode.get(stock.code)?.role.max_position_ratio ? '生成打板预案' : '生成观察预案')}
                      </button>
                    </article>
                  ))}
                </div>
              </section>
            ))}
            {!loading && filteredGroups.length === 0 && <div className="empty-msg">{error || '暂无匹配的涨停股'}</div>}
          </div>
        </div>
      </div>

      <section className="decision-grid">
        <article className="panel">
          <h3>明日预期支撑</h3>
          <div className="rule-list">
            {(data?.summary ?? [error || '等待涨停池同步']).map(item => <span key={item}>{item}</span>)}
          </div>
        </article>
        <article className="panel">
          <h3>观察顺序</h3>
          <p className="plain-text">先看最高板竞价和封单，再看同题材二板晋级，最后看首板是否继续扩散。供应商订单流方向转强的板块与天梯聚类重叠时，优先级提高；该估算不代表账户真实流水。</p>
        </article>
        <article className="panel">
          <h3>数据说明</h3>
          <p className="plain-text">{data?.notes.join('；') ?? '同步中'}</p>
        </article>
      </section>
    </section>
  )
}

function SummaryCard({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return (
    <div className="summary-card">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function sourceLabel(source: string) {
  if (source.includes('unavailable')) return '真实涨停数据源不可用'
  if (source.includes('akshare')) return '东方财富涨停池'
  if (source.includes('eastmoney')) return '东方财富涨停池'
  return source
}

function AtmospherePanel({
  data,
  loading,
  error,
  onSelectTheme,
}: {
  data: LimitUpAtmosphere | null
  loading: boolean
  error: string
  onSelectTheme: (theme: string) => void
}) {
  if (!data) {
    return (
      <section className="limit-atmosphere panel is-missing">
        <div className="atmosphere-head">
          <div><span className="eyebrow">打板风险闸门</span><h3>打板氛围与次日溢价</h3></div>
          <strong className="atmosphere-decision data-gap"><ShieldAlert size={16} />数据不足，禁止打板</strong>
        </div>
        <p className="plain-text">{loading ? '正在同步真实涨停、炸板、跌停及昨日涨停次日表现。' : error || '尚未取得真实统计，不用单只涨停股代替全市场氛围。'}</p>
      </section>
    )
  }

  const metric = data.metrics
  const formatPct = (value: number | null, signed = false) => value == null ? '--' : `${signed && value > 0 ? '+' : ''}${value.toFixed(1)}%`
  return (
    <section className={`limit-atmosphere panel decision-${data.decision.toLowerCase()}`}>
      <div className="atmosphere-head">
        <div>
          <span className="eyebrow">打板风险闸门</span>
          <h3><Activity size={18} />打板氛围与次日溢价</h3>
          <p>用真实涨停/炸板、晋级和昨日涨停次日回报判断接力容错，不把“允许”当作无条件买入。</p>
        </div>
        <strong className={`atmosphere-decision ${data.decision.toLowerCase()}`}>
          <ShieldAlert size={16} />{data.decision_label}
          <small>证据分 {data.score > 0 ? '+' : ''}{data.score} · {data.data_quality}</small>
        </strong>
      </div>

      <div className="atmosphere-metrics">
        <AtmosphereMetric label="涨停 / 跌停" value={`${metric.limit_up_count} / ${metric.limit_down_count ?? '--'}`} />
        <AtmosphereMetric label="封板率 / 炸板率" value={`${formatPct(metric.seal_rate)} / ${formatPct(metric.break_rate)}`} />
        <AtmosphereMetric label="昨日涨停晋级率" value={formatPct(metric.promotion_rate)} detail={metric.promoted_count == null ? '样本缺失' : `${metric.promoted_count}/${metric.previous_limit_up_count}只`} />
        <AtmosphereMetric label="最高连板" value={metric.highest_board ? `${metric.highest_board}板` : '--'} />
        <AtmosphereMetric label="昨日涨停次日平均开盘" value={formatPct(metric.next_day_average_open_pct, true)} detail={`${metric.next_day_open_sample_count}只样本`} />
        <AtmosphereMetric label="昨日涨停低开比例" value={formatPct(metric.next_day_low_open_ratio)} detail={`${metric.next_day_open_sample_count}只样本`} />
        <AtmosphereMetric label="昨日涨停当前/收盘溢价" value={formatPct(metric.next_day_average_premium_pct, true)} detail={`${metric.next_day_premium_sample_count}只样本`} />
        <AtmosphereMetric label="题材集中度" value={formatPct(metric.theme_concentration_pct)} detail={metric.top_theme ? `${metric.top_theme} · ${metric.top_theme_count}只` : '题材聚类缺失'} />
      </div>

      <div className="atmosphere-evidence-grid">
        <div>
          <h4>支持与事实</h4>
          <ul>{data.evidence.map(item => <li key={item}>{item}</li>)}</ul>
        </div>
        <div className="atmosphere-risks">
          <h4>次日被套风险</h4>
          <ul>{(data.risks.length ? data.risks : ['当前未触发全市场禁止项；个股仍需竞价、前排地位与封单确认。']).map(item => <li key={item}>{item}</li>)}</ul>
        </div>
      </div>

      <section className="theme-ladder-intelligence">
        <div className="theme-ladder-heading">
          <div>
            <span className="eyebrow">题材接力结构</span>
            <h4>梯队完整度与个股身份竞争</h4>
          </div>
          <small>{data.role_disclaimer}</small>
        </div>
        {data.theme_ladders.length ? (
          <div className="theme-ladder-grid">
            {data.theme_ladders.slice(0, 6).map(theme => (
              <article className="theme-ladder-card" key={theme.name}>
                <div className="theme-ladder-card-head">
                  <button type="button" onClick={() => onSelectTheme(theme.name)}>{theme.name}</button>
                  <strong>{theme.completeness_score}分</strong>
                </div>
                <div className="theme-mainline-badges">
                  <span className={theme.is_mainline ? 'is-mainline' : theme.is_mainline === false ? 'not-mainline' : 'unknown'}>{theme.mainline_level}</span>
                  <span>{theme.mainline_rank == null ? '排名待确认' : `全市场题材第${theme.mainline_rank}`}</span>
                  <span>{theme.stage}阶段</span>
                  <span>题材仓位上限 {Math.round(theme.max_position_ratio * 100)}%</span>
                </div>
                <p className="theme-stage-reason">{theme.stage_reason}</p>
                <p className="theme-ladder-label">{theme.completeness_label}</p>
                <div className="theme-layer-stats">
                  <span>涨停 <b>{theme.limit_up_count}</b></span>
                  <span>首板 <b>{theme.first_board_count}</b></span>
                  <span>二板 <b>{theme.second_board_count}</b></span>
                  <span>高标 <b>{theme.high_board_count}</b></span>
                  <span>最高 <b>{theme.highest_level}板</b></span>
                  <span>封板率 <b>{theme.seal_rate == null ? '--' : `${theme.seal_rate.toFixed(1)}%`}</b></span>
                </div>
                <strong className={`theme-action ${theme.action.startsWith('禁止') ? 'forbid' : theme.action.startsWith('允许') ? 'allow' : 'caution'}`}>
                  {theme.action}
                </strong>
                <p className="theme-position-rule"><b>阶段—仓位规则：</b>{theme.stage_position_rule}</p>
                <p className="theme-continuation"><b>次日延续：</b>{theme.continuation_expectation}</p>
                {!!theme.evidence.length && (
                  <ul className="theme-mainline-evidence">{theme.evidence.slice(0, 4).map(item => <li key={item}>{item}</li>)}</ul>
                )}
                <div className="identity-list">
                  {theme.identity_roles.slice(0, 5).map(stock => (
                    <div className="identity-row" key={stock.code || stock.name} title={stock.reason}>
                      <span><b>{stock.name}</b><small>{stock.code} · {stock.level}板 · 角色分{stock.role_score}</small></span>
                      <em>{stock.roles.join(' / ')}</em>
                      <small className={`identity-permission risk-${stock.risk_level}`}>{stock.recommended_action} · 上限{Math.round(stock.max_position_ratio * 100)}%</small>
                    </div>
                  ))}
                </div>
                <details className="theme-invalidation">
                  <summary>查看次日失效条件</summary>
                  <ul>{theme.invalidation_conditions.map(item => <li key={item}>{item}</li>)}</ul>
                </details>
              </article>
            ))}
          </div>
        ) : (
          <p className="plain-text">真实涨停题材聚类不足，暂不生成梯队完整度和身份标签。</p>
        )}
      </section>
      <footer className="atmosphere-source">
        <span>统计日 {data.trade_date}{data.previous_trade_date ? ` · 对照日 ${data.previous_trade_date}` : ''}</span>
        <span>{data.missing_data.length ? `缺失：${data.missing_data.join('、')}` : '关键统计已齐全'}</span>
        <span>来源：{sourceLabel(data.source)}</span>
      </footer>
    </section>
  )
}

function AtmosphereMetric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>
}
