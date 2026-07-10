import { useEffect, useState } from 'react'
import { AlertCircle, ExternalLink, TrendingUp, CheckCircle2, XCircle } from 'lucide-react'
import { API_BASE } from '../api'

type IntelItem = {
  id: string
  title: string
  summary: string
  source: string
  published_at: string
  keywords: string[]
  sectors: string[]
  related_stocks: string[]
  strength_score: number
  credibility: string
  fund_status: string
  action: string
  url: string | null
}
type IntelData = {
  source: string
  date: string
  updated_at: string
  items: IntelItem[]
  watchlist: string[]
  data_notes: string[]
}

const statusConfig: Record<string, { icon: typeof CheckCircle2; cls: string; label: string }> = {
  '资金已验证': { icon: CheckCircle2, cls: 'verified', label: '资金已验证' },
  '等资金确认': { icon: AlertCircle, cls: 'pending', label: '等资金确认' },
  '资金流出': { icon: XCircle, cls: 'risk', label: '资金流出' },
}

export default function IntelDesk() {
  const [intel, setIntel] = useState<IntelData | null>(null)
  const [filter, setFilter] = useState<string>('全部')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    fetch(`${API_BASE}/api/intel/daily`)
      .then(r => r.json())
      .then(setIntel)
      .catch(() => {})
      .finally(() => {})
  }, [])

  const items = intel?.items ?? []
  const filtered = filter === '全部' ? items : items.filter(i => i.fund_status === filter)
  const verifiedCnt = items.filter(i => i.fund_status === '资金已验证').length
  const waitingCnt = items.filter(i => i.fund_status === '等资金确认').length
  const riskCnt = items.filter(i => i.fund_status === '资金流出').length

  return (
    <div className="intel-full">
      <header className="intel-top">
        <div>
          <h2>信息差雷达</h2>
          <p>聚合东方财富快讯与新闻联播，先映射板块再用资金流交叉验证。</p>
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
          <AlertCircle size={14} /> 等待确认 <strong>{waitingCnt}</strong>
        </button>
        <button className={`stat-chip risk ${filter === '资金流出' ? 'active' : ''}`} onClick={() => setFilter('资金流出')}>
          <XCircle size={14} /> 资金流出 <strong>{riskCnt}</strong>
        </button>
      </div>

      <div className="intel-grid">
        <div className="intel-cards">
          {filtered.map((item, idx) => {
            const cfg = statusConfig[item.fund_status] ?? statusConfig['等资金确认']
            const isExpanded = expanded.has(item.id)
            return (
              <article className={`intel-card ${isExpanded ? 'expanded' : ''}`} key={item.id}>
                <div className="intel-card-header" onClick={() => {
                  setExpanded(prev => {
                    const next = new Set(prev)
                    if (next.has(item.id)) next.delete(item.id)
                    else next.add(item.id)
                    return next
                  })
                }}>
                  <span className="intel-num">{String(idx + 1).padStart(2, '0')}</span>
                  <div className="intel-card-title">
                    <h3>
                      {item.url ? (
                        <a href={item.url} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()}>
                          {item.title} <ExternalLink size={13} />
                        </a>
                      ) : item.title}
                    </h3>
                    <div className="intel-card-meta">
                      <span>{item.source}</span>
                      <span>{item.published_at}</span>
                      <span>强度 {item.strength_score}</span>
                    </div>
                  </div>
                  <span className={`status-badge ${cfg.cls}`}>
                    <cfg.icon size={14} /> {cfg.label}
                  </span>
                </div>

                {isExpanded && (
                  <div className="intel-card-body">
                    <p className="intel-summary">{item.summary}</p>
                    <div className="intel-tags">
                      {item.sectors.map(s => <span className="tag-sector" key={s}>{s}</span>)}
                      {item.keywords.map(k => <span className="tag-keyword" key={k}>{k}</span>)}
                    </div>
                    {item.related_stocks.length > 0 && (
                      <div className="intel-stocks">
                        <span className="label">相关个股：</span>
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

        <aside className="intel-sidebar">
          <div className="panel">
            <h3><TrendingUp size={15} /> 观察方向</h3>
            <div className="watchlist-tags">
              {(intel?.watchlist?.length ? intel.watchlist : ['等待数据...']).map(s => (
                <span key={s}>{s}</span>
              ))}
            </div>
            <p className="plain-text">只放入观察池，买入仍须主线 + 前排 + 量价 + 止损成立。</p>
          </div>
          <div className="panel">
            <h3>📋 数据说明</h3>
            <div className="rule-list">
              {(intel?.data_notes ?? ['加载中...']).map((n, i) => <span key={i}>{n}</span>)}
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
