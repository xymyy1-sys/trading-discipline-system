import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, ExternalLink, RadioTower, Search, TrendingUp, XCircle } from 'lucide-react'
import { API_BASE } from '../api'

import type { InformationDifferentialOut as IntelData } from '../types'

const statusConfig: Record<string, { icon: typeof CheckCircle2; cls: string; label: string }> = {
  '资金已验证': { icon: CheckCircle2, cls: 'verified', label: '资金已验证' },
  '等资金确认': { icon: AlertCircle, cls: 'pending', label: '等资金确认' },
  '资金流出': { icon: XCircle, cls: 'risk', label: '资金流出' },
}

export default function IntelDesk() {
  const [intel, setIntel] = useState<IntelData | null>(null)
  const [filter, setFilter] = useState<string>('全部')
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    fetch(`${API_BASE}/api/intel/daily`)
      .then(r => r.json())
      .then(setIntel)
      .catch(() => {})
  }, [])

  const items = useMemo(() => intel?.items ?? [], [intel])
  const filtered = useMemo(() => {
    const keyword = query.trim()
    return items.filter(item => {
      const hitStatus = filter === '全部' || item.fund_status === filter
      const hitKeyword = !keyword || `${item.title}${item.summary}${item.sectors.join('')}${item.keywords.join('')}`.includes(keyword)
      return hitStatus && hitKeyword
    })
  }, [filter, items, query])

  const verifiedCnt = items.filter(i => i.fund_status === '资金已验证').length
  const waitingCnt = items.filter(i => i.fund_status === '等资金确认').length
  const riskCnt = items.filter(i => i.fund_status === '资金流出').length
  const topIntel = filtered[0]

  return (
    <div className="intel-full trading-desk-page">
      <header className="intel-command">
        <div className="desk-heading">
          <span className="eyebrow">信息差线索</span>
          <h2>信息差雷达</h2>
          <p>把新闻、快讯和题材映射成候选方向，只做观察池，不替代买点确认。</p>
        </div>
        <div className="intel-meta">
          <span>更新：{intel?.date ?? '--'}</span>
          <span>源：{intel?.source ?? '--'}</span>
        </div>
      </header>

      <div className="intel-stats-bar">
        <button className={`stat-chip ${filter === '全部' ? 'active' : ''}`} onClick={() => setFilter('全部')}>
          全部 <strong>{items.length}</strong>
        </button>
        <button className={`stat-chip verified ${filter === '资金已验证' ? 'active' : ''}`} onClick={() => setFilter('资金已验证')}>
          <CheckCircle2 size={14} /> 已验证 <strong>{verifiedCnt}</strong>
        </button>
        <button className={`stat-chip pending ${filter === '等资金确认' ? 'active' : ''}`} onClick={() => setFilter('等资金确认')}>
          <AlertCircle size={14} /> 等确认 <strong>{waitingCnt}</strong>
        </button>
        <button className={`stat-chip risk ${filter === '资金流出' ? 'active' : ''}`} onClick={() => setFilter('资金流出')}>
          <XCircle size={14} /> 流出 <strong>{riskCnt}</strong>
        </button>
        <label className="search-box intel-search">
          <Search size={15} />
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索题材 / 关键词 / 标题" />
        </label>
      </div>

      <div className="intel-grid">
        <section className="intel-feed panel">
          <div className="panel-title-line">
            <h3><RadioTower size={16} /> 情报流</h3>
            <span>{filtered.length} 条</span>
          </div>
          <div className="intel-cards">
            {filtered.map((item, idx) => {
              const cfg = statusConfig[item.fund_status] ?? statusConfig['等资金确认']
              const isExpanded = expanded.has(item.id)
              return (
                <article className={`intel-card ${isExpanded ? 'expanded' : ''}`} key={item.id}>
                  <button className="intel-card-header" type="button" onClick={() => {
                    setExpanded(prev => {
                      const next = new Set(prev)
                      if (next.has(item.id)) next.delete(item.id)
                      else next.add(item.id)
                      return next
                    })
                  }}>
                    <span className="intel-num">{String(idx + 1).padStart(2, '0')}</span>
                    <div className="intel-card-title">
                      <h3>{item.title}</h3>
                      <div className="intel-card-meta">
                        <span>{item.source}</span>
                        <span>{item.published_at}</span>
                        <span>强度 {item.strength_score}</span>
                      </div>
                    </div>
                    <span className={`status-badge ${cfg.cls}`}>
                      <cfg.icon size={14} /> {cfg.label}
                    </span>
                  </button>

                  {isExpanded && (
                    <div className="intel-card-body">
                      <p className="intel-summary">{item.summary}</p>
                      <div className="intel-tags">
                        {item.sectors.slice(0, 8).map(s => <span className="tag-sector" key={s}>{s}</span>)}
                        {item.keywords.slice(0, 8).map(k => <span className="tag-keyword" key={k}>{k}</span>)}
                      </div>
                      {item.related_stocks.length > 0 && (
                        <div className="intel-stocks">
                          <span className="label">相关标的</span>
                          {item.related_stocks.map(s => <span className="stock-tag" key={s}>{s}</span>)}
                        </div>
                      )}
                      <div className="intel-judgment">{item.action}</div>
                      {item.url && (
                        <a href={item.url} target="_blank" rel="noopener noreferrer" className="intel-link">
                          <ExternalLink size={13} /> 查看原文
                        </a>
                      )}
                    </div>
                  )}
                </article>
              )
            })}
            {filtered.length === 0 && <p className="empty-msg">暂无匹配的信息差条目</p>}
          </div>
        </section>

        <aside className="intel-sidebar">
          <div className="panel intel-focus-panel">
            <h3><TrendingUp size={15} /> 首要观察</h3>
            <strong>{topIntel?.sectors[0] ?? intel?.watchlist?.[0] ?? '等待数据'}</strong>
            <p>{topIntel?.title ?? '暂无高强度信息差'}</p>
            <div className="watchlist-tags">
              {(intel?.watchlist?.length ? intel.watchlist : ['等待数据...']).slice(0, 12).map(s => (
                <span key={s}>{s}</span>
              ))}
            </div>
          </div>
          <div className="panel">
            <h3>纪律提示</h3>
            <div className="rule-list">
              <span>消息只进入观察池，不直接开仓</span>
              <span>必须等待题材资金、核心股和量价确认</span>
              <span>周末消息需等下个交易日竞价验证</span>
            </div>
          </div>
          <div className="panel">
            <h3>数据说明</h3>
            <div className="rule-list">
              {(intel?.data_notes ?? ['加载中...']).map((n, i) => <span key={i}>{n}</span>)}
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
