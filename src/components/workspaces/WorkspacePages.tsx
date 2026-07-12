import { useEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Ban,
  CheckCircle2,
  Clock3,
  ListTodo,
  RefreshCcw,
} from 'lucide-react'
import { API_BASE } from '../../api'
import { chineseEvidence, chineseLabel } from '../../labels'

import type { ActionRecommendation, HoldingOut, InformationItem, IntradayEvidenceEvent, IntradayReview, MarketSeesaw, PositionExecutionState, StockDecisionCard, ThemeRadar } from '../../types'

type WorkspaceModule = {
  key: string
  label: string
  description: string
  Component: ComponentType
}

type WorkspacePageProps = {
  title: string
  subtitle: string
  objective: string
  allowed: string[]
  forbidden: string[]
  modules: WorkspaceModule[]
  defaultModule?: string
  children?: ReactNode
}

export function WorkspacePage({
  title,
  subtitle,
  objective,
  allowed,
  forbidden,
  modules,
  defaultModule,
  children,
}: WorkspacePageProps) {
  const [active, setActive] = useState(defaultModule ?? modules[0]?.key)
  const selected = modules.find(item => item.key === active) ?? modules[0]
  const [visitedModules, setVisitedModules] = useState<Set<string>>(() => new Set(selected?.key ? [selected.key] : []))

  useEffect(() => {
    if (!selected?.key) return
    setVisitedModules(previous => previous.has(selected.key) ? previous : new Set(previous).add(selected.key))
  }, [selected?.key])

  return (
    <section className="workspace-page">
      <header className="workspace-hero">
        <div className="workspace-hero-main">
          <span className="eyebrow">{subtitle}</span>
          <h2>{title}</h2>
          <p>{objective}</p>
        </div>
        <div className="workspace-rules">
          <div>
            <b><CheckCircle2 size={15} />允许</b>
            {allowed.map(item => <span key={item}>{item}</span>)}
          </div>
          <div>
            <b><Ban size={15} />禁止</b>
            {forbidden.map(item => <span key={item}>{item}</span>)}
          </div>
        </div>
      </header>

      {children}

      <nav className="workspace-tabs" aria-label={`${title}二级导航`}>
        {modules.map(item => (
          <button
            key={item.key}
            type="button"
            className={item.key === selected?.key ? 'active' : ''}
            onClick={() => setActive(item.key)}
          >
            <strong>{item.label}</strong>
            <span>{item.description}</span>
          </button>
        ))}
      </nav>

      <section className="workspace-module">
        {modules.map(module => visitedModules.has(module.key) ? (
          <div key={module.key} hidden={module.key !== selected?.key}>
            <module.Component />
          </div>
        ) : null)}
      </section>
    </section>
  )
}

export function TodayDecisionSummary() {
  const [holdings, setHoldings] = useState<HoldingOut[]>([])
  const [executionStates, setExecutionStates] = useState<PositionExecutionState[]>([])
  const [realtimeEvents, setRealtimeEvents] = useState<IntradayEvidenceEvent[]>([])
  const [intradayReviews, setIntradayReviews] = useState<Record<string, IntradayReview>>({})
  const [decisionCards, setDecisionCards] = useState<Record<string, StockDecisionCard>>({})
  const [selectedCode, setSelectedCode] = useState('')
  const [activeAlerts, setActiveAlerts] = useState<ActionRecommendation[]>([])
  const [streamState, setStreamState] = useState('连接中')
  const [streamLastAt, setStreamLastAt] = useState<string | null>(null)
  const [streamNotice, setStreamNotice] = useState('')
  const [streamReconnects, setStreamReconnects] = useState(0)
  const streamInterrupted = useRef(false)
  const [seesaw, setSeesaw] = useState<MarketSeesaw | null>(null)
  const [theme, setTheme] = useState<ThemeRadar | null>(null)
  const [loading, setLoading] = useState(false)
  const [holdingNews, setHoldingNews] = useState<InformationItem[]>([])

  const load = () => {
    setLoading(true)
    Promise.allSettled([
      fetchJsonWithTimeout(`${API_BASE}/api/holdings`),
      fetchJsonWithTimeout(`${API_BASE}/api/holdings/execution-states`),
      fetchJsonWithTimeout(`${API_BASE}/api/market/seesaw-monitor`, 8000),
      fetchJsonWithTimeout(`${API_BASE}/api/market/theme-radar`, 8000),
      fetchJsonWithTimeout(`${API_BASE}/api/alerts/active`),
      fetchJsonWithTimeout(`${API_BASE}/api/intel/daily`, 12000),
    ]).then(results => {
      const [holdingRes, executionRes, seesawRes, themeRes, alertRes, intelRes] = results
      if (holdingRes.status === 'fulfilled' && Array.isArray(holdingRes.value)) {
        setHoldings(holdingRes.value)
        setSelectedCode(current => current || holdingRes.value[0]?.code || '')
        loadIntradayReviews(holdingRes.value)
        loadDecisionCards(holdingRes.value)
      }
      if (executionRes.status === 'fulfilled' && Array.isArray(executionRes.value)) setExecutionStates(executionRes.value)
      if (seesawRes.status === 'fulfilled') setSeesaw(seesawRes.value)
      if (themeRes.status === 'fulfilled') setTheme(themeRes.value)
      if (alertRes.status === 'fulfilled' && Array.isArray(alertRes.value)) setActiveAlerts(alertRes.value)
      if (intelRes.status === 'fulfilled' && Array.isArray(intelRes.value?.items)) setHoldingNews(intelRes.value.items.filter((item: InformationItem) => item.related_holdings?.length).slice(0, 6))
    }).finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  const acknowledgeAlert = (alert: ActionRecommendation) => {
    if (!alert.id) return
    fetch(`${API_BASE}/api/alerts/${alert.id}/acknowledge`, { method: 'POST' })
      .then(response => {
        if (!response.ok) throw new Error('acknowledge failed')
        setActiveAlerts(current => current.filter(item => item.id !== alert.id))
      })
      .catch(() => setStreamNotice('提醒确认失败，请重新连接后再试'))
  }

  useEffect(() => {
    const now = new Date()
    const minutes = now.getHours() * 60 + now.getMinutes()
    if (now.getDay() === 0 || now.getDay() === 6 || minutes < 9 * 60 + 15 || minutes > 15 * 60 + 5) {
      setStreamState('非交易时段，实时推送待机')
      setStreamNotice('盘中交易时段将自动连接；盘外无需保持风险推送长连接。')
      return
    }
    const source = new EventSource(`${API_BASE}/api/intraday-events/stream`, { withCredentials: true })
    source.onopen = () => {
      setStreamState('实时推送已连接')
      if (streamInterrupted.current) {
        setStreamReconnects(value => value + 1)
        setStreamNotice(`连接已恢复 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`)
        streamInterrupted.current = false
      }
    }
    source.onerror = () => {
      streamInterrupted.current = true
      setStreamState('实时推送中断，自动重连中')
      setStreamNotice(`最近中断 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`)
    }
    source.addEventListener('stream-ready', () => {
      setStreamState('实时推送已就绪')
      setStreamLastAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
    })
    source.addEventListener('intraday-risk', event => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as IntradayEvidenceEvent
        setRealtimeEvents(prev => [payload, ...prev.filter(item => item.id !== payload.id)].slice(0, 8))
        setStreamLastAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
        if (payload.target_code) loadSingleIntradayReview(payload.target_code)
      } catch {
        setStreamState('实时事件解析失败')
      }
    })
    return () => source.close()
  }, [])

  const riskStates = useMemo(
    () => executionStates.filter(item => ['EXIT_REQUIRED', 'REDUCE_REQUIRED', 'PROFIT_PROTECTION', 'DIVERGENCE_HOLD'].includes(item.state)),
    [executionStates],
  )
  const highRiskAlerts = (seesaw?.holding_alerts ?? []).filter(item => ['高', '中高', '中'].includes(item.risk_level))
  const totalMarketValue = holdings.reduce((sum, item) => sum + item.market_value, 0)
  const totalProfit = holdings.reduce((sum, item) => sum + item.profit_amount, 0)
  const trackedReviews = holdings.map(holding => ({ holding, review: intradayReviews[holding.code] }))
  const selectedHolding = holdings.find(item => item.code === selectedCode) ?? holdings[0]
  const selectedExecution = executionStates.find(item => item.code === selectedHolding?.code) ?? null
  const selectedCard = selectedHolding ? decisionCards[selectedHolding.code] ?? null : null
  const selectedReview = selectedHolding ? intradayReviews[selectedHolding.code] ?? null : null
  const marketCycle = inferMarketCycle(theme?.market_temperature, seesaw?.market_mode)
  const earningEffect = inferEarningEffect(theme, seesaw)
  const marketLive = isMarketSession()

  return (
    <section className="decision-command">
      <div className="command-card emphasis">
        <span>今日处理优先级</span>
        <strong>{riskStates.length + highRiskAlerts.length}</strong>
        <small>持仓执行风险 + 资金跷跷板告警</small>
      </div>
      <div className="command-card">
        <span>持仓市值</span>
        <strong>{(totalMarketValue / 10000).toFixed(2)} 万</strong>
        <small className={totalProfit >= 0 ? 'num-up' : 'num-down'}>
          总浮盈 {totalProfit >= 0 ? '+' : ''}{totalProfit.toFixed(0)}
        </small>
      </div>
      <div className="command-card">
        <span>市场温度</span>
        <strong>{theme?.market_temperature ?? (marketLive ? '行情源待恢复' : '盘外静态')}</strong>
        <small>最强题材：{theme?.strongest_theme?.name ?? (marketLive ? '等待盘中同步' : '沿用上一交易日基线')}</small>
      </div>
      <div className="command-card">
        <span>资金迁移</span>
        <strong>{seesaw?.market_mode ?? (marketLive ? '等待盘中同步' : '盘外不更新')}</strong>
        <small>{seesaw?.summary ?? (marketLive ? '等待资金跷跷板数据' : '下个交易日开盘后重新计算')}</small>
      </div>

      <div className="panel command-list">
        <header>
          <h3><ListTodo size={16} />当前操作任务</h3>
          <button className="refresh-btn inline" type="button" onClick={load} disabled={loading}>
            <RefreshCcw size={14} />刷新
          </button>
        </header>
        {riskStates.length || highRiskAlerts.length ? (
          <>
            {riskStates.slice(0, 4).map(item => (
              <article key={`exec-${item.holding_id}`}>
                <b>{item.name}</b>
                <span>{chineseEvidence(item.recommended_action)}</span>
                <small>{chineseEvidence(item.evidence[0] ?? chineseLabel(item.volume_price_state))}</small>
              </article>
            ))}
            {highRiskAlerts.slice(0, 4).map(item => (
              <article key={`risk-${item.code}`}>
                <b>{item.name}</b>
                <span>{item.risk_level}风险</span>
                <small>{item.advice || item.signal}</small>
              </article>
            ))}
          </>
        ) : (
          <p className="plain-text">暂无必须处理的风险任务。继续按盘前计划执行，不主动扩大仓位。</p>
        )}
      </div>

      <div className="panel command-list">
        <header>
          <h3><AlertTriangle size={16} />证据变化</h3>
        </header>
        {(seesaw?.inflow_targets ?? []).slice(0, 3).map(item => (
          <article key={item.name}>
            <b>{item.name}</b>
            <span className="num-up">流入 {item.net_inflow.toFixed(2)} 亿</span>
            <small>{item.evidence}</small>
          </article>
        ))}
        {!seesaw?.inflow_targets?.length && <p className="plain-text">{marketLive ? '暂无已确认的资金迁移证据。' : '非交易时段不产生新的资金迁移证据；开盘后根据板块资金曲线更新。'}</p>}
      </div>

      <div className="panel command-list realtime-events">
        <header>
          <h3><AlertTriangle size={16} />实时风险事件</h3>
          <span className="stream-state">{streamState}{streamLastAt ? ` · ${streamLastAt}` : ''}</span>
        </header>
        {streamNotice && <p className="stream-health-notice">{streamNotice}{streamReconnects ? ` · 已恢复 ${streamReconnects} 次` : ''}</p>}
        {realtimeEvents.length ? (
          realtimeEvents.map(event => (
            <article key={`${event.id}-${event.event_type}`}>
              <b>{event.target_name || event.target_code}</b>
              <span>{chineseLabel(event.event_type)}</span>
              <small>{chineseEvidence(event.evidence?.[0] ?? `${chineseLabel(event.severity)} / 优先级 ${event.priority}`)}</small>
            </article>
          ))
        ) : (
          <p className="plain-text">暂无新推送事件；后台采集或手动刷新触发后会实时出现。</p>
        )}
      </div>

      <section className="panel cockpit-overview">
        <header>
          <h3><Activity size={16} />全市场决策状态</h3>
          <span className="stream-state">规则推断 · 数据刷新后更新</span>
        </header>
        <div className="cockpit-market-grid">
          <div><span>赚钱效应</span><strong>{earningEffect}</strong><small>{theme?.strongest_theme?.stage_reason || '等待题材扩散和涨停质量证据'}</small></div>
          <div><span>情绪周期</span><strong>{marketCycle}</strong><small>根据市场温度、主线强度和资金迁移综合判断</small></div>
          <div><span>主线方向</span><strong>{theme?.strongest_theme?.name || '--'}</strong><small>{theme?.strongest_theme ? `强度 ${theme.strongest_theme.score} · 排名 ${theme.strongest_theme.rank}` : '等待真实题材数据'}</small></div>
          <div><span>资金轮动</span><strong>{seesaw?.market_mode || '--'}</strong><small>{seesaw?.summary || '等待资金流证据'}</small></div>
        </div>
      </section>

      <section className="panel holding-cockpit">
        <header>
          <h3><ListTodo size={16} />持仓预期与盘中证据驾驶舱</h3>
          <span className="stream-state">选择持仓查看完整决策链</span>
        </header>
        <div className="holding-cockpit-tabs">
          {holdings.map(item => (
            <button type="button" key={item.code} className={item.code === selectedHolding?.code ? 'active' : ''} onClick={() => setSelectedCode(item.code)}>
              <strong>{item.name}</strong><span>{item.code}</span>
            </button>
          ))}
        </div>
        {selectedHolding && (
          <div className="cockpit-detail-grid">
            <article>
              <h4>预期阶段与动态验证</h4>
              {selectedCard ? <>
                <p><b>当前阶段：</b>{selectedCard.expectation.stage || '待建立'}</p>
                <p><b>盘前预期：</b>{chineseLabel(selectedCard.expectation.base_expectation)}</p>
                <p><b>实际表现：</b>{chineseLabel(selectedCard.expectation.expectation_result)}</p>
                <p><b>状态变化：</b>{chineseLabel(selectedCard.expectation.state_transition)}</p>
                <p><b>预期差：</b>{selectedCard.expectation.expectation_gap_score.toFixed(1)} 分 · 可信度 {(selectedCard.expectation.confidence * 100).toFixed(0)}%</p>
                <small>合理开盘区间 {selectedCard.expectation.expected_open_low.toFixed(2)}% ～ {selectedCard.expectation.expected_open_high.toFixed(2)}%</small>
              </> : <p>正在读取该持仓的预期快照。</p>}
            </article>
            <article>
              <h4>个股量价确认</h4>
              {selectedCard?.volume_price ? <>
                <p><b>当前价 / 分时均价：</b>{selectedCard.volume_price.price.toFixed(2)} / {selectedCard.volume_price.vwap.toFixed(2)}</p>
                <p><b>量价状态：</b>{chineseLabel(selectedCard.volume_price.pattern)}</p>
                <p><b>高点回撤：</b>{selectedCard.volume_price.high_drawdown.toFixed(2)}%</p>
                <p><b>量比 / 上攻效率：</b>{selectedCard.volume_price.volume_ratio.toFixed(2)} / {selectedCard.volume_price.attack_efficiency.toFixed(2)}</p>
                <small>{selectedCard.volume_price.vwap_reliable ? '真实分钟数据已确认' : '分钟数据不足，结论已降级'}</small>
              </> : <p>暂无可靠量价快照，不生成确定性结论。</p>}
            </article>
            <article>
              <h4>当前操作与风险边界</h4>
              {selectedExecution ? <>
                <p><b>状态：</b>{chineseLabel(selectedExecution.state)}</p>
                <p><b>建议：</b>{chineseEvidence(selectedExecution.recommended_action)}</p>
                <p><b>建议仓位：</b>{(selectedExecution.recommended_position_ratio * 100).toFixed(0)}% · 可卖 {selectedExecution.sellable_quantity} 股</p>
                <p><b>结构 / 硬止损：</b>{selectedExecution.structure_stop_price.toFixed(2)} / {selectedExecution.hard_stop_price.toFixed(2)}</p>
                <small>{selectedExecution.invalid_conditions[0] || '等待失效条件确认'}</small>
              </> : <p>暂无持仓执行状态。</p>}
            </article>
            <article className="cockpit-evidence-card">
              <h4>证据、反向证据与恢复条件</h4>
              <div><b>支持证据</b>{(selectedExecution?.evidence ?? selectedCard?.evidence ?? []).slice(0, 3).map(item => <p key={item}>+ {chineseEvidence(item)}</p>)}</div>
              <div><b>反向证据</b>{(selectedExecution?.counter_evidence ?? selectedCard?.counter_evidence ?? []).slice(0, 3).map(item => <p key={item}>- {chineseEvidence(item)}</p>)}</div>
              <div><b>恢复条件</b>{(selectedExecution?.recovery_conditions ?? []).slice(0, 3).map(item => <p key={item}>· {chineseEvidence(item)}</p>)}</div>
            </article>
            <article className="cockpit-timeline-card">
              <h4>盘中事件时间线</h4>
              <div className="cockpit-timeline">
                {(selectedReview?.timeline ?? selectedCard?.timeline ?? []).slice(0, 8).map(event => (
                  <div key={`${event.id}-${event.captured_at}`}><time>{formatEventTime(event.captured_at)}</time><b>{chineseLabel(event.event_type)}</b><span>{chineseEvidence(event.evidence?.[0] || chineseLabel(event.severity))}</span></div>
                ))}
              </div>
              {!(selectedReview?.timeline ?? selectedCard?.timeline ?? []).length && <p>暂无盘中采样事件。</p>}
            </article>
          </div>
        )}
      </section>

      <div className="panel command-list active-recommendations">
        <header><h3><CheckCircle2 size={16} />待确认操作建议</h3><span className="stream-state">{activeAlerts.length} 条</span></header>
        {activeAlerts.length ? activeAlerts.map(alert => (
          <article key={alert.id ?? `${alert.code}-${alert.created_at}`}>
            <b>{alert.name || alert.code}</b>
            <span>{chineseLabel(alert.level)} · {chineseEvidence(alert.action)}</span>
            <small>{decisionCards[alert.code] ? `预期：${chineseLabel(decisionCards[alert.code].expectation.expectation_result)} · 预期差 ${decisionCards[alert.code].expectation.expectation_gap_score.toFixed(1)}；` : ''}{chineseEvidence(alert.evidence[0] || chineseLabel(alert.state))}</small>
            <button type="button" className="alert-ack-button" onClick={() => acknowledgeAlert(alert)}>已阅读并确认</button>
          </article>
        )) : <p className="plain-text">当前没有待确认操作建议。</p>}
      </div>

      <div className="panel command-list holding-news-alerts">
        <header><h3><AlertTriangle size={16} />持仓资讯提醒</h3><span className="stream-state">公告、突发新闻与政策关联</span></header>
        {holdingNews.length ? holdingNews.map(item => <article key={item.id}>
          <b>{item.related_holdings.join('、')}</b><span className={item.sentiment === '利好' ? 'num-up' : item.sentiment === '利空' ? 'num-down' : ''}>{item.sentiment} · {item.title}</span>
          <small>{item.sentiment_reason}；{item.action}</small>
          {item.url && <a href={item.url} target="_blank" rel="noreferrer">查看原文</a>}
        </article>) : <p className="plain-text">暂未发现与当前持仓直接关联的新公告或突发新闻。</p>}
      </div>

      <div className="panel command-list evidence-trajectory">
        <header>
          <h3><Activity size={16} />全持仓证据摘要</h3>
          <span className="stream-state">点击持仓切换上方完整时间线 · {holdings.length} 只</span>
        </header>
        {trackedReviews.length ? (
          trackedReviews.map(({ holding, review }) => (
            <button type="button" className={`trajectory-card trajectory-summary ${review ? '' : 'trajectory-empty'}`} key={holding.code} onClick={() => setSelectedCode(holding.code)}>
              <div className="trajectory-head">
                <b>{holding.name || holding.code}</b>
                <span>{review ? chineseEvidence(review.latest_action || chineseLabel(review.latest_state)) : '等待盘中采样'}</span>
                <small>{review ? chineseLabel(review.data_quality) : `${holding.code} · 当前无证据快照`}</small>
              </div>
              <div className="trajectory-summary-latest">
                <strong>{review?.timeline[0] ? chineseLabel(review.timeline[0].event_type) : '暂无事件'}</strong>
                <span>{review?.timeline[0] ? formatEventTime(review.timeline[0].captured_at) : '--:--'}</span>
                <small>{review ? `共 ${review.timeline.length} 个关键证据点` : '等待行情源'}</small>
              </div>
            </button>
          ))
        ) : (
          <p className="plain-text">暂无盘中证据轨迹。后台采集器运行后会展示价格、分时均价、预期状态和动作建议的时间线。</p>
        )}
      </div>
    </section>
  )

  function loadIntradayReviews(nextHoldings: HoldingOut[]) {
    if (!nextHoldings.length) {
      setIntradayReviews({})
      return
    }
    Promise.allSettled(
      nextHoldings.map(item =>
        fetchJsonWithTimeout(`${API_BASE}/api/stocks/${item.code}/intraday-review`, 15000)
          .then(review => [item.code, review] as const),
      ),
    ).then(results => {
      const next: Record<string, IntradayReview> = {}
      results.forEach(result => {
        if (result.status === 'fulfilled') {
          const [code, review] = result.value
          next[code] = review as IntradayReview
        }
      })
      setIntradayReviews(next)
    })
  }

  function loadDecisionCards(nextHoldings: HoldingOut[]) {
    Promise.allSettled(nextHoldings.map(item =>
      fetchJsonWithTimeout(`${API_BASE}/api/stocks/${item.code}/decision-card`, 8000).then(card => [item.code, card] as const),
    )).then(results => {
      const next: Record<string, StockDecisionCard> = {}
      results.forEach(result => { if (result.status === 'fulfilled') next[result.value[0]] = result.value[1] })
      setDecisionCards(next)
    })
  }

  function loadSingleIntradayReview(code: string) {
    fetchJsonWithTimeout(`${API_BASE}/api/stocks/${code}/intraday-review`, 15000)
      .then(review => {
        setIntradayReviews(prev => ({ ...prev, [code]: review as IntradayReview }))
      })
      .catch(() => undefined)
  }
}

function fetchJsonWithTimeout(url: string, timeoutMs = 5000) {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  return fetch(url, { signal: controller.signal })
    .then(r => r.json())
    .finally(() => window.clearTimeout(timeout))
}

function formatEventTime(value: string) {
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return value
  return time.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function inferMarketCycle(temperature?: string, marketMode?: string) {
  const text = `${temperature || ''}${marketMode || ''}`
  if (/退潮|冰点|极弱|防守/.test(text)) return '退潮防守'
  if (/高潮|过热|加速/.test(text)) return '高潮分歧'
  if (/强|活跃|主升/.test(text)) return '主升活跃'
  return '轮动分歧'
}

function isMarketSession() {
  const now = new Date()
  const minutes = now.getHours() * 60 + now.getMinutes()
  return now.getDay() >= 1 && now.getDay() <= 5 && minutes >= 9 * 60 + 15 && minutes <= 15 * 60
}

function inferEarningEffect(theme: ThemeRadar | null, seesaw: MarketSeesaw | null) {
  const strongThemes = theme?.themes.filter(item => item.score >= 70).length ?? 0
  const inflowTargets = seesaw?.inflow_targets.filter(item => item.net_inflow > 0).length ?? 0
  if (strongThemes >= 3 && inflowTargets >= 3) return '较强'
  if (strongThemes >= 1 || inflowTargets >= 2) return '局部活跃'
  if (!theme && !seesaw) return '--'
  return '偏弱'
}

export function WorkspaceLinkCard({ title, desc, onClick }: { title: string; desc: string; onClick: () => void }) {
  return (
    <button type="button" className="workspace-link-card" onClick={onClick}>
      <span>{title}</span>
      <small>{desc}</small>
      <ArrowRight size={15} />
    </button>
  )
}

export function CalibrationPlaceholder() {
  return (
    <section className="workspace-placeholder panel">
      <h3><Clock3 size={16} />复盘校准能力分阶段开放</h3>
      <p>
        当前阶段保留交易日志和月度复盘入口。预期胜率、量价模型有效性、做T真实贡献、参数自动校准属于 P2，
        等 P0/P1 验收缺口补齐后再接入。
      </p>
    </section>
  )
}
