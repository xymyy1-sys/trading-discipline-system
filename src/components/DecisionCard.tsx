import { useEffect, useRef, useState } from 'react'
import { RefreshCcw, Search } from 'lucide-react'
import { API_BASE } from '../api'
import { chineseEvidence, chineseLabel } from '../labels'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type { ExpectationRule, HoldingOut, StockDecisionCard } from '../types'
import AiInsightButton from './AiInsightButton'

type DecisionCardMode = 'watchlist' | 'holding'
type WatchlistStock = { code: string; name: string; score: number; tier: string }

echarts.use([LineChart, BarChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

export default function DecisionCard({ mode = 'watchlist' }: { mode?: DecisionCardMode }) {
  const [code, setCode] = useState('')
  const [card, setCard] = useState<StockDecisionCard | null>(null)
  const [holdings, setHoldings] = useState<HoldingOut[]>([])
  const [watchlist, setWatchlist] = useState<WatchlistStock[]>([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [rules, setRules] = useState<ExpectationRule[]>([])
  const [showRules, setShowRules] = useState(false)

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
    const loadTargets = () => fetch(mode === 'holding' ? `${API_BASE}/api/holdings` : `${API_BASE}/api/watchlist-recommendations`)
      .then(r => r.json())
      .then((data: HoldingOut[] | WatchlistStock[]) => {
        if (mode === 'holding') setHoldings(data as HoldingOut[])
        else setWatchlist((data as WatchlistStock[]).slice(0, 10))
        if (data[0]) {
          setCode(data[0].code)
          loadCard(data[0].code)
        }
      })
      .catch(() => {})
    loadTargets()
    const syncWatchlist = () => { if (mode === 'watchlist') loadTargets() }
    window.addEventListener('watchlist-updated', syncWatchlist)
    fetch(`${API_BASE}/api/expectation-rules`)
      .then(r => r.json())
      .then((data: ExpectationRule[]) => setRules(data))
      .catch(() => setRules([]))
    return () => window.removeEventListener('watchlist-updated', syncWatchlist)
  }, [mode])

  const updateRule = (id: number, patch: Partial<ExpectationRule>) => {
    setRules(current => current.map(rule => rule.id === id ? { ...rule, ...patch } : rule))
  }

  const saveRule = (rule: ExpectationRule) => {
    setMessage('')
    fetch(`${API_BASE}/api/expectation-rules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rule),
    })
      .then(async response => {
        if (!response.ok) throw new Error((await response.json()).detail || '阈值保存失败')
        return response.json()
      })
      .then((saved: ExpectationRule) => {
        updateRule(saved.id, saved)
        setMessage(`${saved.display_name || saved.base_expectation} 阈值已保存`)
      })
      .catch(error => setMessage(error instanceof Error ? error.message : '阈值保存失败'))
  }

  return (
    <section className={`decision-page ${mode === 'holding' ? 'holding-decision-page' : ''}`}>
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
          <AiInsightButton scope="stock" target={card?.code || code.trim()} />
        </div>
      </header>
      {message && <p className="refresh-note">{message}</p>}

      <section className="panel expectation-rule-panel">
        <div className="selected-theme-head">
          <div><strong>预期阈值模板</strong><span>可按交易剧本、阶段和基础预期覆盖默认开盘区间。</span></div>
          <button className="refresh-btn inline" type="button" onClick={() => setShowRules(value => !value)}>{showRules ? '收起' : '编辑阈值'}</button>
        </div>
        {showRules && <div className="expectation-rule-grid">
          {rules.map(rule => (
            <article key={rule.id} className="expectation-rule-card">
              <header><b>{rule.display_name || rule.base_expectation}</b><span>{rule.script_type} · {rule.stage}</span></header>
              <label>合理低值<input type="number" step="0.1" value={rule.expected_open_low} onChange={e => updateRule(rule.id, { expected_open_low: Number(e.target.value) })} /></label>
              <label>合理高值<input type="number" step="0.1" value={rule.expected_open_high} onChange={e => updateRule(rule.id, { expected_open_high: Number(e.target.value) })} /></label>
              <label>超预期<input type="number" step="0.1" value={rule.outperform_threshold} onChange={e => updateRule(rule.id, { outperform_threshold: Number(e.target.value) })} /></label>
              <label>低于预期<input type="number" step="0.1" value={rule.underperform_threshold} onChange={e => updateRule(rule.id, { underperform_threshold: Number(e.target.value) })} /></label>
              <label>严重低于<input type="number" step="0.1" value={rule.severe_underperform_threshold} onChange={e => updateRule(rule.id, { severe_underperform_threshold: Number(e.target.value) })} /></label>
              <button type="button" onClick={() => saveRule(rule)}>保存</button>
            </article>
          ))}
        </div>}
      </section>

      {mode === 'holding' && holdings.length > 0 && (
        <div className="decision-holding-strip">
          {holdings.slice(0, 10).map(item => (
            <button key={item.id} className={card?.code === item.code ? 'active' : ''} type="button" onClick={() => loadCard(item.code)}>
              {item.name}<span>{item.code}</span>
            </button>
          ))}
        </div>
      )}

      {mode === 'watchlist' && watchlist.length > 0 && (
        <div className="decision-holding-strip">
          {watchlist.map(item => (
            <button key={item.code} className={card?.code === item.code ? 'active' : ''} type="button" onClick={() => loadCard(item.code)}>
              {item.name}<span>{item.code} · {item.score}分</span>
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
                <span>{card.industry || '行业待确认'} · {(card.concepts || []).slice(0, 5).join('、') || '概念待确认'} · {chineseLabel(card.data_quality)}</span>
              </div>
              <div className="decision-price">
                <strong className={card.change_pct >= 0 ? 'num-up' : 'num-down'}>{card.current_price.toFixed(2)}</strong>
                <span className={card.change_pct >= 0 ? 'num-up' : 'num-down'}>{card.change_pct >= 0 ? '+' : ''}{card.change_pct.toFixed(2)}%</span>
              </div>
            </div>

            <div className="decision-kpi-grid">
              <div><b>基础预期</b><span>{chineseLabel(card.expectation.base_expectation)}</span></div>
              <div><b>实际表现</b><span>{chineseLabel(card.expectation.expectation_result)}</span></div>
              <div><b>状态变化</b><span>{chineseEvidence(card.expectation.state_transition)}</span></div>
              <div><b>预期差</b><span>{card.expectation.expectation_gap_score}</span></div>
              <div><b>合理开盘</b><span>{card.expectation.expected_open_low.toFixed(1)}% - {card.expectation.expected_open_high.toFixed(1)}%</span></div>
              <div><b>可信度</b><span>{(card.expectation.confidence * 100).toFixed(0)}%</span></div>
            </div>

            <ExpectationJourney card={card} />

            {mode === 'holding' && <DecisionMinuteChart card={card} />}

            {mode === 'holding' && card.volume_price && (
              <div className="decision-section volume-price-section">
                <b>量价快照 · {card.volume_price.stage}</b>
                <p>{chineseLabel(card.volume_price.pattern)} · {chineseLabel(card.volume_price.data_quality)} · {chineseLabel(card.volume_price.data_source || '行情源待确认')}</p>
                {card.volume_price.active_flow_estimated && <p className="refresh-note">主动买卖额按分钟价格方向推导，并非逐笔盘口原始主动成交。</p>}
                <div className="volume-price-grid">
                  <div><b>分时均价</b><span>{card.volume_price.vwap ? card.volume_price.vwap.toFixed(2) : '--'}</span></div>
                  <div><b>偏离VWAP</b><span className={card.volume_price.price_vs_vwap >= 0 ? 'num-up' : 'num-down'}>{card.volume_price.price_vs_vwap >= 0 ? '+' : ''}{card.volume_price.price_vs_vwap.toFixed(2)}%</span></div>
                  <div><b>高点回撤</b><span>{card.volume_price.high_drawdown.toFixed(2)}%</span></div>
                  <div><b>成交额</b><span>{card.volume_price.amount.toFixed(2)}亿</span></div>
                  <div><b>估算全天</b><span>{card.volume_price.estimated_full_day_amount.toFixed(2)}亿</span></div>
                  <div><b>流通盘换手</b><span>{card.volume_price.turnover ? `${card.volume_price.turnover.toFixed(2)}%${card.volume_price.turnover_reliable ? '' : '（待核）'}` : '--'}</span></div>
                  <div><b>流通市值</b><span>{card.volume_price.float_cap ? `${card.volume_price.float_cap.toFixed(2)}亿` : '--'}</span></div>
                  <div><b>上攻段</b><span>{card.volume_price.attack_amount.toFixed(2)}亿</span></div>
                  <div><b>回落段</b><span>{card.volume_price.pullback_amount.toFixed(2)}亿</span></div>
                  <div><b>回落卖出</b><span>{card.volume_price.pullback_sell_ratio.toFixed(1)}%</span></div>
                  <div><b>MA5 / 10 / 20</b><span>{card.volume_price.ma5.toFixed(2)} / {card.volume_price.ma10.toFixed(2)} / {card.volume_price.ma20.toFixed(2)}</span></div>
                  <div><b>5日 / 10日涨幅</b><span>{card.volume_price.return_5d.toFixed(1)}% / {card.volume_price.return_10d.toFixed(1)}%</span></div>
                  <div><b>距20日高点</b><span>{card.volume_price.distance_recent_high_pct.toFixed(1)}%</span></div>
                  <div><b>历史量比</b><span>{card.volume_price.historical_volume_ratio ? card.volume_price.historical_volume_ratio.toFixed(2) : '--'}</span></div>
                  <div><b>获利筹码估算</b><span>{card.volume_price.chip_profit_ratio.toFixed(1)}%</span></div>
                  <div><b>筹码平均成本</b><span>{card.volume_price.chip_avg_cost ? card.volume_price.chip_avg_cost.toFixed(2) : '--'}</span></div>
                  <div><b>逐笔大单净额</b><span>{card.volume_price.large_order_threshold ? `${card.volume_price.large_order_net_amount.toFixed(3)}亿` : '--'}</span></div>
                </div>
                {card.volume_price.chip_metrics_estimated && <p className="refresh-note">筹码指标按近30日日线成交量加权估算，不冒充行情源官方筹码分布。</p>}
                <ul>
                  {(card.volume_price.evidence.length ? card.volume_price.evidence : ['暂无明确量价偏离。']).slice(0, 5).map(item => <li key={item}>{item}</li>)}
                </ul>
              </div>
            )}

            {mode === 'holding' && card.consensus_risk && (
              <div className="decision-section">
                <b>获利盘与一致性风险 · {card.consensus_risk.level} · {card.consensus_risk.score}</b>
                <ul>
                  {(card.consensus_risk.factors.length ? card.consensus_risk.factors : card.consensus_risk.counter_evidence).slice(0, 5).map(item => <li key={item}>{item}</li>)}
                </ul>
                <p>{card.consensus_risk.actions.join('；')}</p>
              </div>
            )}

            <div className="decision-section">
              <b>预期建议</b>
              <p>{card.expectation.suggestion}</p>
              <ul>
                {(card.expectation.evidence.length ? card.expectation.evidence : ['暂无明显预期偏离，按计划观察。']).slice(0, 5).map(item => <li key={item}>{item}</li>)}
              </ul>
            </div>

            {mode === 'holding' && card.execution_state && (
              <div className="decision-section execution-conclusion">
                <b>执行结论</b>
                <p>{chineseEvidence(card.execution_state.recommended_action)} · {chineseLabel(card.execution_state.state)}</p>
                <div className="execution-status-row">
                  <span>预期 {chineseLabel(card.execution_state.expectation_state)}</span>
                  <span>量价 {chineseLabel(card.execution_state.volume_price_state)}</span>
                  <span>板块 {chineseEvidence(card.execution_state.sector_state || '待确认')}</span>
                </div>
                <div className="execution-line-grid">
                  <div><b>结构止损</b><span>{card.execution_state.structure_stop_price.toFixed(2)}</span></div>
                  <div><b>硬止损</b><span>{card.execution_state.hard_stop_price.toFixed(2)}</span></div>
                  <div><b>止损来源</b><span>{stopSourceLabel(card.execution_state.stop_source)}</span></div>
                  <div><b>利润保护</b><span>{card.execution_state.profit_protection_price ? card.execution_state.profit_protection_price.toFixed(2) : '--'}</span></div>
                </div>
                <p className="execution-stop-source">{card.execution_state.stop_source_detail || '止损来源待下一次状态刷新确认。'}</p>
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

          {mode === 'holding' && <aside className="panel decision-side">
            <h3>允许 / 禁止动作</h3>
            <div className="decision-action-lists">
              <div>
                <b>允许</b>
                {card.allowed_actions.map(item => <span key={item}>{chineseEvidence(item)}</span>)}
              </div>
              <div>
                <b>禁止</b>
                {card.forbidden_actions.map(item => <span key={item}>{chineseEvidence(item)}</span>)}
              </div>
            </div>
            <h3>做T资格</h3>
            {card.t_eligibility ? (
              <div className="decision-t-box">
                <strong>{card.t_eligibility.eligible ? chineseLabel(card.t_eligibility.t_type) : '禁止做T'}</strong>
                <p>可卖 {card.t_eligibility.sellable_quantity.toLocaleString()} 股，建议 {card.t_eligibility.suggested_quantity.toLocaleString()} 股。</p>
                <p>接回区间 {card.t_eligibility.buyback_price_low.toFixed(2)} - {card.t_eligibility.buyback_price_high.toFixed(2)}</p>
                <ul>
                  {(card.t_eligibility.eligible ? card.t_eligibility.buyback_conditions : card.t_eligibility.forbidden_reasons).slice(0, 5).map(item => <li key={item}>{chineseEvidence(item)}</li>)}
                </ul>
              </div>
            ) : <p className="plain-text">非持仓股不生成做T计划。</p>}
          </aside>}

          {mode === 'holding' && <section className="panel decision-timeline">
            <h3>证据时间线</h3>
            {card.timeline.length ? card.timeline.map(item => (
              <article key={`${item.event_type}-${item.captured_at}`}>
                <time>{new Date(item.captured_at).toLocaleTimeString('zh-CN', { hour12: false })}</time>
                <strong>{chineseLabel(item.event_type)}</strong>
                <span>{chineseLabel(item.severity)}</span>
                <p>{chineseEvidence(item.evidence[0] || `${item.value} / ${item.previous_value}`)}</p>
              </article>
            )) : <p className="plain-text">暂无盘中事件，刷新持仓执行后会自动沉淀。</p>}
          </section>}
        </div>
      ) : (
        <div className="panel"><p className="plain-text">输入股票代码后生成个股决策卡。</p></div>
      )}
    </section>
  )
}

function ExpectationJourney({ card }: { card: StockDecisionCard }) {
  const expectation = card.expectation
  const actualOpen = expectation.actual_open_pct
  const openingText = actualOpen === 0 && expectation.state_transition === 'WAITING_VALIDATION'
    ? '等待下一交易日集合竞价'
    : `${actualOpen >= 0 ? '+' : ''}${actualOpen.toFixed(2)}%`
  const gapText = expectation.state_transition === 'WAITING_VALIDATION'
    ? '尚未验证'
    : `${chineseEvidence(expectation.state_transition)}（预期差 ${expectation.expectation_gap_score >= 0 ? '+' : ''}${expectation.expectation_gap_score}）`

  return (
    <section className="expectation-journey">
      <div className="expectation-journey-head">
        <div><strong>预期管理链</strong><span>收盘形成基准，竞价、开盘和盘中逐阶段验证并修正。</span></div>
        <span className="expectation-date">验证日 {expectation.trade_date}</span>
      </div>
      <div className="expectation-journey-grid">
        <article><small>① 收盘基准</small><b>{chineseLabel(expectation.base_expectation)}</b><p>{expectation.evidence[0] || '根据收盘量价、题材和资金证据自动推演。'}</p></article>
        <article><small>② 次日合理开盘</small><b>{expectation.expected_open_low.toFixed(1)}% ～ {expectation.expected_open_high.toFixed(1)}%</b><p>区间外才构成显著预期差，不只看涨跌颜色。</p></article>
        <article><small>③ 集合竞价 / 开盘</small><b>{openingText}</b><p>{expectation.stage === '次日盘前预期' ? '等待真实竞价后更新。' : `当前阶段：${expectation.stage}`}</p></article>
        <article><small>④ 实际与预期差</small><b>{gapText}</b><p>当前涨幅 {expectation.actual_change_pct >= 0 ? '+' : ''}{expectation.actual_change_pct.toFixed(2)}%</p></article>
        <article><small>⑤ 执行修正</small><b>{chineseLabel(expectation.expectation_result)}</b><p>{expectation.suggestion}</p></article>
      </div>
    </section>
  )
}

function DecisionMinuteChart({ card }: { card: StockDecisionCard }) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!ref.current || !card.minute_chart.length) return
    const chart = echarts.init(ref.current)
    const times = card.minute_chart.map(item => item.time)
    const events = card.timeline.map(item => {
      const time = new Date(item.captured_at).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
      const point = card.minute_chart.find(row => row.time === time)
      return point ? [time, point.price, item.event_type] : null
    }).filter((item): item is [string, number, string] => item !== null)
    chart.setOption({
      tooltip: { trigger: 'axis' }, legend: { data: ['价格', 'VWAP', '分钟成交额'] },
      grid: [{ left: 52, right: 28, top: 38, height: '58%' }, { left: 52, right: 28, top: '76%', height: '15%' }],
      xAxis: [{ type: 'category', data: times, boundaryGap: false }, { type: 'category', data: times, gridIndex: 1, axisLabel: { show: false } }],
      yAxis: [{ type: 'value', scale: true }, { type: 'value', gridIndex: 1, name: '亿元' }],
      series: [
        { name: '价格', type: 'line', data: card.minute_chart.map(item => item.price), showSymbol: false, lineStyle: { width: 2 } },
        { name: 'VWAP', type: 'line', data: card.minute_chart.map(item => item.vwap), showSymbol: false, lineStyle: { type: 'dashed' } },
        { name: '事件', type: 'scatter', data: events, symbolSize: 9, tooltip: { formatter: (params: { data: [string, number, string] }) => `${params.data[0]} ${params.data[2]}` } },
        { name: '分钟成交额', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: card.minute_chart.map(item => item.amount) },
      ],
    })
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => { window.removeEventListener('resize', resize); chart.dispose() }
  }, [card])
  if (!card.minute_chart.length) return <p className="refresh-note">暂无真实分钟线，图表不使用模拟数据补齐。</p>
  const estimated = card.minute_chart.some(item => item.amount_estimated)
  return <div className="decision-section"><b>分钟量价与事件轨迹</b><div ref={ref} style={{ height: 360 }} />{estimated && <p className="refresh-note">备用源分钟成交额为估算值，仅用于观察，不作为可靠 VWAP 触发依据。</p>}</div>
}

function stopSourceLabel(source: string) {
  const labels: Record<string, string> = {
    next_day_plan: '次日计划',
    sell_card: '卖出卡',
    text_script: '交易剧本',
    fallback_candidate: '候选价兜底',
  }
  const parts = (source || 'fallback_candidate').split('+').filter(Boolean)
  return parts.map(part => labels[part] ?? part).join(' + ') || labels.fallback_candidate
}
