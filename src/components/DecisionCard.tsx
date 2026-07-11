import { useEffect, useState } from 'react'
import { RefreshCcw, Search } from 'lucide-react'
import { API_BASE } from '../api'

import type { HoldingOut, StockDecisionCard } from '../types'

export default function DecisionCard() {
  const [code, setCode] = useState('')
  const [card, setCard] = useState<StockDecisionCard | null>(null)
  const [holdings, setHoldings] = useState<HoldingOut[]>([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')

  const loadCard = (target = code) => {
    const normalized = target.trim()
    if (!normalized) {
      setMessage('先输入股票代码或选择持仓。')
      return
    }
    setLoading(true)
    setMessage('')
    fetch(`${API_BASE}/api/stocks/${normalized}/decision-card`)
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((data: StockDecisionCard) => {
        setCard(data)
        setCode(data.code)
      })
      .catch(() => setMessage('个股决策卡读取失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetch(`${API_BASE}/api/holdings`)
      .then(r => r.json())
      .then((data: HoldingOut[]) => {
        setHoldings(data)
        if (data[0]) {
          setCode(data[0].code)
          loadCard(data[0].code)
        }
      })
      .catch(() => {})
  }, [])

  return (
    <section className="decision-page">
      <header className="pos-header">
        <div>
          <h2>个股决策卡</h2>
          <p>把盘前预期、实际表现、持仓执行、事件时间线和做T资格合并到一张卡。</p>
        </div>
        <div className="decision-search">
          <input value={code} onChange={e => setCode(e.target.value)} placeholder="输入股票代码" />
          <button className="grade-btn" type="button" onClick={() => loadCard()} disabled={loading}>
            {loading ? <RefreshCcw size={16} /> : <Search size={16} />}
            查询
          </button>
        </div>
      </header>
      {message && <p className="refresh-note">{message}</p>}

      {holdings.length > 0 && (
        <div className="decision-holding-strip">
          {holdings.slice(0, 10).map(item => (
            <button key={item.id} className={card?.code === item.code ? 'active' : ''} type="button" onClick={() => loadCard(item.code)}>
              {item.name}<span>{item.code}</span>
            </button>
          ))}
        </div>
      )}

      {card ? (
        <div className="decision-grid">
          <section className="panel decision-main">
            <div className="selected-theme-head">
              <div>
                <strong>{card.name} <span className="mono">{card.code}</span></strong>
                <span>{card.industry || '行业待确认'} · {(card.concepts || []).slice(0, 5).join('、') || '概念待确认'} · {card.data_quality}</span>
              </div>
              <div className="decision-price">
                <strong className={card.change_pct >= 0 ? 'num-up' : 'num-down'}>{card.current_price.toFixed(2)}</strong>
                <span className={card.change_pct >= 0 ? 'num-up' : 'num-down'}>{card.change_pct >= 0 ? '+' : ''}{card.change_pct.toFixed(2)}%</span>
              </div>
            </div>

            <div className="decision-kpi-grid">
              <div><b>基础预期</b><span>{card.expectation.base_expectation}</span></div>
              <div><b>实际表现</b><span>{card.expectation.expectation_result}</span></div>
              <div><b>状态变化</b><span>{card.expectation.state_transition}</span></div>
              <div><b>预期差</b><span>{card.expectation.expectation_gap_score}</span></div>
              <div><b>合理开盘</b><span>{card.expectation.expected_open_low.toFixed(1)}% - {card.expectation.expected_open_high.toFixed(1)}%</span></div>
              <div><b>可信度</b><span>{(card.expectation.confidence * 100).toFixed(0)}%</span></div>
            </div>

            {card.volume_price && (
              <div className="decision-section volume-price-section">
                <b>量价快照 · {card.volume_price.stage}</b>
                <p>{card.volume_price.pattern} · {card.volume_price.data_quality} · {card.volume_price.data_source || '行情源待确认'}</p>
                <div className="volume-price-grid">
                  <div><b>VWAP</b><span>{card.volume_price.vwap ? card.volume_price.vwap.toFixed(2) : '--'}</span></div>
                  <div><b>偏离VWAP</b><span className={card.volume_price.price_vs_vwap >= 0 ? 'num-up' : 'num-down'}>{card.volume_price.price_vs_vwap >= 0 ? '+' : ''}{card.volume_price.price_vs_vwap.toFixed(2)}%</span></div>
                  <div><b>高点回撤</b><span>{card.volume_price.high_drawdown.toFixed(2)}%</span></div>
                  <div><b>成交额</b><span>{card.volume_price.amount.toFixed(2)}亿</span></div>
                  <div><b>估算全天</b><span>{card.volume_price.estimated_full_day_amount.toFixed(2)}亿</span></div>
                  <div><b>换手</b><span>{card.volume_price.turnover ? `${card.volume_price.turnover.toFixed(2)}%` : '--'}</span></div>
                </div>
                <ul>
                  {(card.volume_price.evidence.length ? card.volume_price.evidence : ['暂无明确量价偏离。']).slice(0, 5).map(item => <li key={item}>{item}</li>)}
                </ul>
              </div>
            )}

            <div className="decision-section">
              <b>预期建议</b>
              <p>{card.expectation.suggestion}</p>
              <ul>
                {(card.expectation.evidence.length ? card.expectation.evidence : ['暂无明显预期偏离，按计划观察。']).slice(0, 5).map(item => <li key={item}>{item}</li>)}
              </ul>
            </div>

            {card.execution_state && (
              <div className="decision-section execution-conclusion">
                <b>执行结论</b>
                <p>{card.execution_state.recommended_action} · {card.execution_state.state}</p>
                <div className="execution-status-row">
                  <span>预期 {card.execution_state.expectation_state}</span>
                  <span>量价 {card.execution_state.volume_price_state}</span>
                  <span>板块 {card.execution_state.sector_state || '待确认'}</span>
                </div>
                <div className="execution-line-grid">
                  <div><b>结构止损</b><span>{card.execution_state.structure_stop_price.toFixed(2)}</span></div>
                  <div><b>硬止损</b><span>{card.execution_state.hard_stop_price.toFixed(2)}</span></div>
                  <div><b>利润保护</b><span>{card.execution_state.profit_protection_price ? card.execution_state.profit_protection_price.toFixed(2) : '--'}</span></div>
                </div>
                <div className="execution-rule-columns">
                  <div>
                    <b>禁止条件</b>
                    <ul>
                      {card.execution_state.invalid_conditions.slice(0, 3).map(item => <li key={item}>{item}</li>)}
                    </ul>
                  </div>
                  <div>
                    <b>修复条件</b>
                    <ul>
                      {card.execution_state.recovery_conditions.slice(0, 3).map(item => <li key={item}>{item}</li>)}
                    </ul>
                  </div>
                </div>
              </div>
            )}
          </section>

          <aside className="panel decision-side">
            <h3>允许 / 禁止动作</h3>
            <div className="decision-action-lists">
              <div>
                <b>允许</b>
                {card.allowed_actions.map(item => <span key={item}>{item}</span>)}
              </div>
              <div>
                <b>禁止</b>
                {card.forbidden_actions.map(item => <span key={item}>{item}</span>)}
              </div>
            </div>
            <h3>做T资格</h3>
            {card.t_eligibility ? (
              <div className="decision-t-box">
                <strong>{card.t_eligibility.eligible ? card.t_eligibility.t_type : '禁止做T'}</strong>
                <p>可卖 {card.t_eligibility.sellable_quantity.toLocaleString()} 股，建议 {card.t_eligibility.suggested_quantity.toLocaleString()} 股。</p>
                <p>接回区间 {card.t_eligibility.buyback_price_low.toFixed(2)} - {card.t_eligibility.buyback_price_high.toFixed(2)}</p>
                <ul>
                  {(card.t_eligibility.eligible ? card.t_eligibility.buyback_conditions : card.t_eligibility.forbidden_reasons).slice(0, 5).map(item => <li key={item}>{item}</li>)}
                </ul>
              </div>
            ) : <p className="plain-text">非持仓股不生成做T计划。</p>}
          </aside>

          <section className="panel decision-timeline">
            <h3>证据时间线</h3>
            {card.timeline.length ? card.timeline.map(item => (
              <article key={`${item.event_type}-${item.captured_at}`}>
                <time>{new Date(item.captured_at).toLocaleTimeString('zh-CN', { hour12: false })}</time>
                <strong>{item.event_type}</strong>
                <span>{item.severity}</span>
                <p>{item.evidence[0] || `${item.value} / ${item.previous_value}`}</p>
              </article>
            )) : <p className="plain-text">暂无盘中事件，刷新持仓执行后会自动沉淀。</p>}
          </section>
        </div>
      ) : (
        <div className="panel"><p className="plain-text">输入股票代码后生成个股决策卡。</p></div>
      )}
    </section>
  )
}
