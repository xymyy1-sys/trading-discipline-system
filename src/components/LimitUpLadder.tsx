import { useCallback, useEffect, useMemo, useState } from 'react'
import { CalendarClock, Flame, NotebookPen, RefreshCcw, Search, Trophy } from 'lucide-react'
import { API_BASE } from '../api'
import { cachedJson } from '../apiCache'

import type {
  LimitUpStock,
  LimitUpLadder as LimitUpLadderData,
} from '../types'

export default function LimitUpLadder() {
  const [data, setData] = useState<LimitUpLadderData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creatingCode, setCreatingCode] = useState<string | null>(null)
  const [activeConcept, setActiveConcept] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  const loadData = useCallback((force = false) => {
    setLoading(true)
    setError('')
    cachedJson<LimitUpLadderData>(
      'limit-up-ladder',
      `${API_BASE}/api/market/limit-up-ladder${force ? '?force_refresh=true' : ''}`,
      force,
    )
      .then(({ data }) => setData(data))
      .catch(() => setError('涨停天梯暂不可用，请确认后端服务和行情源'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    loadData()
    const timer = setInterval(() => loadData(), 300000)
    return () => clearInterval(timer)
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

  const highest = data?.groups[0]?.level ?? 0
  const total = data?.groups.reduce((sum, group) => sum + group.stocks.length, 0) ?? 0
  const topCluster = data?.clusters[0]
  const isRecentTradeDay = !!data?.notes.some(note => note.includes('非交易日') || note.includes('最近交易日'))

  const createAuctionPlan = (stock: LimitUpStock, level: number) => {
    setCreatingCode(stock.code)
    fetch(`${API_BASE}/api/next-day-plans/from-limit-up`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...stock, level, max_position_ratio: 0.1 }),
    })
      .then(r => r.json())
      .then(() => {
        window.dispatchEvent(new CustomEvent('nav', { detail: '次日计划卡' }))
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
                        {creatingCode === stock.code ? '生成中' : '生成打板预案'}
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
          <p className="plain-text">先看最高板竞价和封单，再看同题材二板晋级，最后看首板是否继续扩散。资金流入板块和天梯聚类重叠时，优先级提高。</p>
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
  if (source.includes('diagnostic')) return '诊断数据'
  if (source.includes('akshare')) return '东方财富涨停池'
  return source
}
