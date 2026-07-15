import { Component, useEffect, useMemo, useRef, useState, type ComponentType, type ErrorInfo, type ReactNode } from 'react'
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
import { intradayEventSemantics, isActionableIntradayEvent } from '../../eventSemantics'
import { buildConsensusHighOpenFadeView } from '../../consensusHighOpenFade'

import type {
  ActionRecommendation,
  ConsensusHighOpenFade,
  GlobalMarketCues,
  HoldingExecutionSignal,
  HoldingOut,
  InformationItem,
  IntradayEvidenceEvent,
  IntradayReview,
  MarketRegime,
  MarketSeesaw,
  OpportunityRadar,
  PositionExecutionState,
  ReflexivityAssessment,
  StockDecisionCard,
  ThemeRadar,
} from '../../types'
import AiInsightButton from '../AiInsightButton'
import FlowKineticsEvidence from '../FlowKineticsEvidence'
import PositionAiAssistant from '../PositionAiAssistant'
import { holdingFlowKineticsFields } from '../../flowKinetics'

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

class WorkspaceModuleErrorBoundary extends Component<
  { children: ReactNode; moduleName: string },
  { failed: boolean }
> {
  state = { failed: false }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`工作区模块“${this.props.moduleName}”渲染失败`, error, info)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="workspace-module-error" role="alert">
        <strong>{this.props.moduleName}暂时无法显示</strong>
        <span>已隔离本模块错误，其他菜单仍可继续使用。</span>
        <button type="button" onClick={() => this.setState({ failed: false })}>重试本模块</button>
      </div>
    )
  }

  static getDerivedStateFromError() {
    return { failed: true }
  }
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
            <WorkspaceModuleErrorBoundary moduleName={module.label}>
              <module.Component />
            </WorkspaceModuleErrorBoundary>
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
  const holdingCodesRef = useRef<Set<string>>(new Set())
  const holdingDecisionRefreshedAt = useRef<Record<string, number>>({})
  const marketReflexivityLoadedAt = useRef(0)
  const stockReflexivityLoadedAt = useRef<Record<string, number>>({})
  const [seesaw, setSeesaw] = useState<MarketSeesaw | null>(null)
  const [theme, setTheme] = useState<ThemeRadar | null>(null)
  const [marketRegime, setMarketRegime] = useState<MarketRegime | null>(null)
  const [globalCues, setGlobalCues] = useState<GlobalMarketCues | null>(null)
  const [opportunityRadar, setOpportunityRadar] = useState<OpportunityRadar | null>(null)
  const [marketReflexivity, setMarketReflexivity] = useState<ReflexivityAssessment | null>(null)
  const [stockReflexivity, setStockReflexivity] = useState<Record<string, ReflexivityAssessment>>({})
  const [loading, setLoading] = useState(false)
  const [holdingNews, setHoldingNews] = useState<InformationItem[]>([])
  const [showMarketEvidence, setShowMarketEvidence] = useState(false)

  const load = (forceRefresh = false) => {
    setLoading(true)
    const refreshQuery = forceRefresh ? '?force_refresh=true' : ''
    const marketPromise = fetchJsonWithTimeout(`${API_BASE}/api/market/regime${refreshQuery}`, 45000)
      .then(value => {
        setMarketRegime(value as MarketRegime)
        // 反身性接口复用刚完成的市场快照。串行调用可避免两个全市场
        // 强制刷新请求并发采集、重复写入同一时点快照。
        return fetchJsonWithTimeout(`${API_BASE}/api/market/reflexivity`, 12000)
      })
      .then(value => {
        setMarketReflexivity(value as ReflexivityAssessment)
        marketReflexivityLoadedAt.current = Date.now()
        if (forceRefresh && selectedCode) loadStockReflexivity(selectedCode, true)
      })
    const globalPromise = fetchJsonWithTimeout(`${API_BASE}/api/market/global-cues${refreshQuery}`, 45000)
      .then(value => setGlobalCues(value as GlobalMarketCues))
    const opportunityPromise = fetchJsonWithTimeout(`${API_BASE}/api/intel/opportunity-radar${refreshQuery}`, 45000)
      .then(value => setOpportunityRadar(value as OpportunityRadar))
    const workspacePromise = Promise.allSettled([
      fetchJsonWithTimeout(`${API_BASE}/api/holdings`),
      fetchJsonWithTimeout(`${API_BASE}/api/holdings/execution-states`),
      fetchJsonWithTimeout(`${API_BASE}/api/market/seesaw-monitor${refreshQuery}`, 12000),
      fetchJsonWithTimeout(`${API_BASE}/api/market/theme-radar${refreshQuery}`, 12000),
      fetchJsonWithTimeout(`${API_BASE}/api/alerts/active`),
      fetchJsonWithTimeout(`${API_BASE}/api/intel/daily${refreshQuery}`, 15000),
    ]).then(results => {
      const [holdingRes, executionRes, seesawRes, themeRes, alertRes, intelRes] = results
      if (holdingRes.status === 'fulfilled' && Array.isArray(holdingRes.value)) {
        setHoldings(holdingRes.value)
        holdingCodesRef.current = new Set(holdingRes.value.map((item: HoldingOut) => item.code))
        setSelectedCode(current => holdingRes.value.some((item: HoldingOut) => item.code === current)
          ? current
          : holdingRes.value[0]?.code || '')
        loadIntradayReviews(holdingRes.value)
        loadDecisionCards(holdingRes.value)
      }
      if (executionRes.status === 'fulfilled' && Array.isArray(executionRes.value)) {
        setExecutionStates(executionRes.value)
        const holdingRiskEvents = (executionRes.value as PositionExecutionState[])
          .flatMap(item => item.events ?? [])
          .filter(isRiskEvent)
          .sort((left, right) => +new Date(right.captured_at) - +new Date(left.captured_at))
          .slice(0, 8)
        setRealtimeEvents(holdingRiskEvents)
      }
      if (seesawRes.status === 'fulfilled') setSeesaw(seesawRes.value)
      if (themeRes.status === 'fulfilled') setTheme(themeRes.value)
      if (alertRes.status === 'fulfilled' && Array.isArray(alertRes.value)) setActiveAlerts(alertRes.value)
      if (intelRes.status === 'fulfilled' && Array.isArray(intelRes.value?.items)) setHoldingNews(intelRes.value.items.filter((item: InformationItem) => item.related_holdings?.length).slice(0, 6))
    })
    void Promise.allSettled([marketPromise, globalPromise, opportunityPromise, workspacePromise])
      .finally(() => setLoading(false))
  }

  const loadRef = useRef(load)
  loadRef.current = load

  // 初次装载一次；后续刷新由按钮、SSE 与独立定时器触发。
  useEffect(() => {
    loadRef.current(false)
  }, [])

  useEffect(() => {
    if (!selectedCode) return
    const loadedAt = stockReflexivityLoadedAt.current[selectedCode] ?? 0
    if (stockReflexivity[selectedCode] && Date.now() - loadedAt < 5 * 60_000) return
    loadStockReflexivity(selectedCode, false)
  }, [selectedCode, stockReflexivity])

  useEffect(() => {
    const refreshReflexivity = () => {
      if (!isMarketSession()) return
      if (Date.now() - marketReflexivityLoadedAt.current >= 5 * 60_000) {
        fetchJsonWithTimeout(`${API_BASE}/api/market/reflexivity`, 15000)
          .then(value => {
            setMarketReflexivity(value as ReflexivityAssessment)
            marketReflexivityLoadedAt.current = Date.now()
          })
          .catch(() => undefined)
      }
      if (selectedCode && Date.now() - (stockReflexivityLoadedAt.current[selectedCode] ?? 0) >= 5 * 60_000) {
        loadStockReflexivity(selectedCode, true)
      }
    }
    const timer = window.setInterval(refreshReflexivity, 60_000)
    return () => window.clearInterval(timer)
  }, [selectedCode])

  useEffect(() => {
    const refreshIntradayOpportunity = () => {
      if (!isMarketSession() || document.visibilityState !== 'visible') return
      // The API keeps provider-specific caches, so a one-minute cockpit poll
      // discovers new limit-up bursts and flow turns without forcing upstream
      // market requests on every menu switch.
      void Promise.allSettled([
        fetchJsonWithTimeout(`${API_BASE}/api/intel/opportunity-radar`, 30000)
          .then(value => setOpportunityRadar(value as OpportunityRadar)),
        fetchJsonWithTimeout(`${API_BASE}/api/market/seesaw-monitor`, 15000)
          .then(value => setSeesaw(value as MarketSeesaw)),
      ])
    }
    const timer = window.setInterval(refreshIntradayOpportunity, 60_000)
    return () => window.clearInterval(timer)
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
    let source: EventSource | null = null

    const closeStream = () => {
      source?.close()
      source = null
    }
    const connectWhenTrading = () => {
      if (!isMarketSession(true)) {
        closeStream()
        setStreamState('非交易时段，实时推送待机')
        setStreamNotice('盘中交易时段将自动连接；盘外无需保持风险推送长连接。')
        return
      }
      if (source && source.readyState !== EventSource.CLOSED) return

      source = new EventSource(`${API_BASE}/api/intraday-events/stream`, { withCredentials: true })
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
          if (!holdingCodesRef.current.has(payload.target_code) || !isRiskEvent(payload)) return
          setRealtimeEvents(prev => [payload, ...prev.filter(item => item.id !== payload.id)].slice(0, 8))
          setStreamLastAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
          if (payload.target_code) refreshHoldingDecision(payload.target_code)
        } catch {
          setStreamState('实时事件解析失败')
        }
      })
    }

    connectWhenTrading()
    // 页面可能在 9:15 前打开；定时重评可在进入交易时段后自动建立长连接。
    const sessionTimer = window.setInterval(connectWhenTrading, 30_000)
    return () => {
      window.clearInterval(sessionTimer)
      closeStream()
    }
  }, [])

  const riskStates = useMemo(
    () => executionStates.filter(item => ['EXIT_REQUIRED', 'REDUCE_REQUIRED', 'EXPECTATION_INVALIDATED', 'EXPECTATION_VOLUME_BREAKDOWN', 'PROFIT_PROTECTION', 'DIVERGENCE_HOLD'].includes(item.state) || item.recommended_reduce_ratio > 0),
    [executionStates],
  )
  const urgentHoldingSignals = useMemo(() => executionStates.flatMap(execution => {
    const signals = [execution.high_sell_signal, execution.panic_sell_guard, execution.contrarian_add_signal]
      .filter((signal): signal is HoldingExecutionSignal => Boolean(signal))
      .filter(signal => signal.status === 'ACTIVE' || signal.status === 'ELIGIBLE')
    return signals.map(signal => ({ execution, signal }))
  }), [executionStates])
  const expectationRisks = useMemo(() => holdings.flatMap(holding => {
    const card = decisionCards[holding.code]
    if (!card || !['INVALID', 'WEAKER'].includes(card.expectation.expectation_result)) return []
    const execution = executionStates.find(item => item.code === holding.code)
    return [{ holding, card, execution }]
  }), [holdings, decisionCards, executionStates])
  const highRiskAlerts = (seesaw?.holding_alerts ?? []).filter(item => ['高', '中高', '中'].includes(item.risk_level))
  const riskTargetCount = new Set([...riskStates.map(item => item.code), ...expectationRisks.map(item => item.holding.code), ...highRiskAlerts.map(item => item.code), ...urgentHoldingSignals.map(item => item.execution.code)]).size
  const totalMarketValue = holdings.reduce((sum, item) => sum + item.market_value, 0)
  const totalProfit = holdings.reduce((sum, item) => sum + item.profit_amount, 0)
  const trackedReviews = holdings.map(holding => ({ holding, review: intradayReviews[holding.code] }))
  const selectedHolding = holdings.find(item => item.code === selectedCode) ?? holdings[0]
  const selectedExecution = executionStates.find(item => item.code === selectedHolding?.code) ?? null
  const selectedCard = selectedHolding ? decisionCards[selectedHolding.code] ?? null : null
  const selectedReview = selectedHolding ? intradayReviews[selectedHolding.code] ?? null : null
  const marketCycle = marketRegime?.regime_name ?? inferMarketCycle(theme?.market_temperature, seesaw?.market_mode)
  const earningEffect = marketRegime ? marketEffectLabel(marketRegime.opportunity_score) : inferEarningEffect(theme, seesaw)
  const marketRiskActive = Boolean(marketRegime && ['极高', '高', '中高'].includes(marketRegime.risk_level))
  const marketLive = isMarketSession()
  const consensusHighOpenFade = marketReflexivity?.consensus_high_open_fade
    ?? opportunityRadar?.consensus_high_open_fade
    ?? null
  const intradayExpansion = opportunityRadar?.intraday_expansion ?? null
  const expansionQuality = String(intradayExpansion?.data_quality || '').toLowerCase()
  const expansionUnavailable = !intradayExpansion || ['missing', 'degraded', 'unavailable', 'error'].includes(expansionQuality)

  return (
    <section className="decision-command">
      <div className="ai-market-action"><AiInsightButton scope="market" target="today" label="AI全局复核" /></div>
      <div className="command-card emphasis">
        <span>今日处理优先级</span>
        <strong>{riskTargetCount}</strong>
        <small>持仓执行风险 + 资金跷跷板告警</small>
      </div>
      <div className="command-card sensitive-card">
        <span>持仓市值</span>
        <strong>{(totalMarketValue / 10000).toFixed(2)} 万</strong>
        <small className={totalProfit >= 0 ? 'num-up' : 'num-down'}>
          总浮盈 {totalProfit >= 0 ? '+' : ''}{totalProfit.toFixed(0)}
        </small>
      </div>
      <div className="command-card">
        <span>全市场状态</span>
        <strong className={marketRiskActive ? 'risk-text' : ''}>{marketCycle}</strong>
        <small>{marketRegime ? `赚钱 ${marketRegime.opportunity_score} · 亏钱 ${marketRegime.loss_score} · 风险${marketRegime.risk_level}` : '等待全A真实广度与量能'}</small>
      </div>
      <div className="command-card">
        <span>全市场主力净流</span>
        <strong className={(marketRegime?.market_main_net_inflow_yi ?? 0) < 0 ? 'num-down' : 'num-up'}>{formatSignedNumber(marketRegime?.market_main_net_inflow_yi, ' 亿')}</strong>
        <small>{marketRegime ? `上涨 ${marketRegime.up_count ?? '--'} · 下跌 ${marketRegime.down_count ?? '--'} · 涨停/跌停 ${marketRegime.limit_up_count ?? '--'}/${marketRegime.limit_down_count ?? '--'}` : '等待全市场资金与涨跌家数'}</small>
      </div>

      <div className="panel command-list">
        <header>
          <h3><ListTodo size={16} />当前操作任务</h3>
          <button className="refresh-btn inline" type="button" onClick={() => load(true)} disabled={loading}>
            <RefreshCcw size={14} />刷新
          </button>
        </header>
        {marketRiskActive || expectationRisks.length || riskStates.length || highRiskAlerts.length || urgentHoldingSignals.length ? (
          <>
            {urgentHoldingSignals.slice(0, 8).map(({ execution, signal }) => (
              <article key={`holding-signal-${execution.code}-${signal.code}`} className={`holding-action-signal ${holdingSignalTone(signal)}`}>
                <b>{execution.name} · {signal.title}</b>
                <span>{signal.action}</span>
                <small className="sensitive-evidence">{signal.evidence[0] || signal.missing_conditions[0] || '等待下一份真实量价快照确认。'}</small>
                <details><summary>查看触发依据与撤销条件</summary><div className="decision-basis">
                  {signal.evidence.slice(0, 5).map(item => <p className="sensitive-evidence" key={item}>依据：{chineseEvidence(item)}</p>)}
                  {signal.cancel_conditions.slice(0, 3).map(item => <p key={item}>撤销/升级：{chineseEvidence(item)}</p>)}
                  {signal.recovery_conditions.slice(0, 2).map(item => <p key={item}>后续复核：{chineseEvidence(item)}</p>)}
                </div></details>
              </article>
            ))}
            {marketRiskActive && marketRegime && (
              <article className={riskTone(marketRegime.risk_level)}>
                <b>全市场执行闸门</b>
                <span>{marketRegime.regime_name} · {marketRegime.risk_level}风险</span>
                <small>{(marketRegime.forbidden_actions ?? []).join('；') || '市场证据不足，禁止主动扩大风险。'}</small>
                <details><summary>查看真实数据依据</summary><div className="decision-basis">{(marketRegime.evidence ?? []).map(item => <p key={item}>依据：{item}</p>)}{(marketRegime.allowed_actions ?? []).map(item => <p key={item}>允许：{item}</p>)}</div></details>
              </article>
            )}
            {expectationRisks.slice(0, 5).map(({ holding, card, execution }) => (
              <article key={`expectation-${holding.code}`} className={`expectation-risk-task ${riskTone(execution?.recommendation?.level || (card.expectation.expectation_result === 'INVALID' ? 'EXIT' : 'PROTECT'))}`}>
                <b>{holding.name}</b>
                <span>{card.expectation.expectation_result === 'INVALID' ? '预期证伪' : '弱于预期'} · {chineseEvidence(execution?.recommended_action || card.expectation.suggestion)}</span>
                <small className="sensitive-evidence">合理开盘 {card.expectation.expected_open_low.toFixed(2)}%～{card.expectation.expected_open_high.toFixed(2)}%，实际 {card.expectation.actual_open_pct >= 0 ? '+' : ''}{card.expectation.actual_open_pct.toFixed(2)}%，预期差 {card.expectation.expectation_gap_score}。</small>
                <details><summary>查看决策依据与动态复核条件</summary><DecisionBasis execution={execution} fallback={card.expectation.evidence} /></details>
              </article>
            ))}
            {riskStates.filter(item => !expectationRisks.some(risk => risk.holding.code === item.code) && !urgentHoldingSignals.some(entry => entry.execution.code === item.code)).slice(0, 4).map(item => (
              <article key={`exec-${item.holding_id}`} className={riskTone(item.recommendation?.level || item.state)}>
                <b>{item.name}</b>
                <span>{chineseEvidence(item.recommended_action)}</span>
                <small className="sensitive-evidence">{chineseEvidence(item.evidence[0] ?? chineseLabel(item.volume_price_state))}</small>
                <details><summary>为什么是这个建议？</summary><DecisionBasis execution={item} /></details>
              </article>
            ))}
            {highRiskAlerts.slice(0, 4).map(item => (
              <article key={`risk-${item.code}`} className={riskTone(item.risk_level)}>
                <b>{item.name}</b>
                <span>{item.risk_level}风险</span>
                <small className="sensitive-evidence">{item.advice || item.signal}</small>
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
            <FlowKineticsEvidence fields={item} compact label="板块资金" />
          </article>
        ))}
        {(seesaw?.holding_alerts ?? []).slice(0, 3).map(item => (
          <article className="holding-flow-evidence" key={`holding-flow-${item.code}`}>
            <b>{item.name} · {item.primary_industry_sector || item.holding_theme || '所属板块'}</b>
            <span>{item.sector_flow_signal || '等待板块资金形成可验证拐点'}</span>
            <small>当前板块净流 {item.sector_net_inflow >= 0 ? '+' : ''}{item.sector_net_inflow.toFixed(2)} 亿</small>
            <FlowKineticsEvidence fields={holdingFlowKineticsFields(item)} compact label="持仓资金" />
          </article>
        ))}
        {!seesaw?.inflow_targets?.length && !seesaw?.holding_alerts?.length && <p className="plain-text">{marketLive ? '暂无已确认的资金迁移证据。' : '非交易时段不产生新的资金迁移证据；开盘后根据板块资金曲线更新。'}</p>}
      </div>

      <div className="panel command-list realtime-events">
        <header>
          <h3><AlertTriangle size={16} />实时风险与机会事件</h3>
          <span className="stream-state">{streamState}{streamLastAt ? ` · ${streamLastAt}` : ''}</span>
        </header>
        {streamNotice && <p className="stream-health-notice">{streamNotice}{streamReconnects ? ` · 已恢复 ${streamReconnects} 次` : ''}</p>}
        {realtimeEvents.length ? (
          realtimeEvents.map(event => (
            <article key={`${event.id}-${event.event_type}`} className={intradayEventSemantics(event.event_type, event.severity).toneClass}>
              <b>{event.target_name || event.target_code}</b>
              <span>{chineseLabel(event.event_type)} · {riskActionForEvent(event, executionStates)}</span>
              <small className="sensitive-evidence">{riskDetailForEvent(event, decisionCards, executionStates)}</small>
            </article>
          ))
        ) : (
          <p className="plain-text">暂无新推送事件；后台采集或手动刷新触发后会实时出现。</p>
        )}
      </div>

      <section className="panel cockpit-overview">
        <header>
          <h3><Activity size={16} />全市场决策状态</h3>
          <span className="stream-state">全A真实广度 · {marketRegime?.data_quality === 'complete' ? '数据完整' : marketRegime ? '缺口降级' : '等待同步'}</span>
        </header>
        <div className="cockpit-market-grid">
          <div className={marketRiskActive ? 'market-state-danger' : ''}><span>赚钱 / 亏钱效应</span><strong>{earningEffect} / {marketRegime ? marketLossLabel(marketRegime.loss_score) : '--'}</strong><small>{marketRegime ? `机会 ${marketRegime.opportunity_score} · 亏钱 ${marketRegime.loss_score}` : '等待全A真实广度'}</small><button type="button" className="market-evidence-link" onClick={() => setShowMarketEvidence(value => !value)}>查看计算依据</button></div>
          <div><span>市场状态</span><strong>{marketCycle}</strong><small>{marketRegime ? `风险${marketRegime.risk_level} · 可信度 ${(marketRegime.confidence * 100).toFixed(0)}%` : '等待量能、广度和指数共振'}</small></div>
          <div><span>上涨 / 下跌家数</span><strong>{marketRegime?.up_count ?? '--'} / {marketRegime?.down_count ?? '--'}</strong><small>涨停 / 跌停 {marketRegime?.limit_up_count ?? '--'} / {marketRegime?.limit_down_count ?? '--'} · 中位涨幅 {formatSignedNumber(marketRegime?.median_change_pct, '%')}</small></div>
          <div><span>成交额与量能</span><strong>{formatNumber(marketRegime?.turnover_yi, ' 亿')}</strong><small>较前日 {formatRatio(marketRegime?.volume_ratio_previous)} · 较5日均量 {formatRatio(marketRegime?.volume_ratio_5d)}</small></div>
          <div><span>全市场主力净流</span><strong className={(marketRegime?.market_main_net_inflow_yi ?? 0) < 0 ? 'num-down' : 'num-up'}>{formatSignedNumber(marketRegime?.market_main_net_inflow_yi, ' 亿')}</strong><small>指数合成涨跌 {formatSignedNumber(marketRegime?.index_composite_change_pct, '%')}</small></div>
          <div><span>行业扩散 / 集中</span><strong>{formatRatio(marketRegime?.positive_sector_ratio)} / {formatRatio(marketRegime?.top3_inflow_share)}</strong><small>正向行业占比 / 前三流入集中度</small></div>
          <div><span>主线方向</span><strong>{theme?.strongest_theme?.name || marketRegime?.strongest_sectors?.[0]?.name || '--'}</strong><small>{theme?.strongest_theme ? `题材强度 ${theme.strongest_theme.score}` : '按行业资金与价格确认'}</small></div>
          <div><span>资金轮动</span><strong>{seesaw?.market_mode || '--'}</strong><small>{seesaw?.summary || '等待资金流证据'}</small><button type="button" className="market-evidence-link" onClick={() => setShowMarketEvidence(value => !value)}>查看资金明细</button></div>
        </div>
        {showMarketEvidence && <div className="market-evidence-panel">
          <h4>全市场结论计算依据</h4>
          {(marketRegime?.evidence ?? []).map(item => <p key={item}>· {item}</p>)}
          {(marketRegime?.allowed_actions ?? []).length ? <p><b>当前允许：</b>{marketRegime?.allowed_actions.join('；')}</p> : null}
          {(marketRegime?.forbidden_actions ?? []).length ? <p className="risk-text"><b>当前禁止：</b>{marketRegime?.forbidden_actions.join('；')}</p> : null}
          <p><b>指数证据：</b>{(marketRegime?.indices ?? []).length ? marketRegime?.indices.map(item => `${item.name} ${formatSignedNumber(item.change_pct, '%')}${item.above_vwap === null ? '' : item.above_vwap ? '（均价线上）' : '（均价线下）'}`).join('；') : '指数数据缺失。'}</p>
          <p><b>行业流入：</b>{(marketRegime?.strongest_sectors ?? []).length ? marketRegime?.strongest_sectors.map(item => `${item.name} ${formatSignedNumber(item.net_inflow, '亿')}`).join('；') : '暂无已确认流入行业。'}</p>
          <p><b>行业流出：</b>{(marketRegime?.weakest_sectors ?? []).length ? marketRegime?.weakest_sectors.map(item => `${item.name} ${formatSignedNumber(item.net_inflow, '亿')}`).join('；') : '暂无已确认流出行业。'}</p>
          <p><b>数据来源：</b>{marketRegime?.source || '等待全市场同步'}。更新时间：{marketRegime?.captured_at ? new Date(marketRegime.captured_at).toLocaleString('zh-CN') : '--'}。</p>
          {(marketRegime?.missing_fields ?? []).length ? <small className="num-down">缺失字段：{marketRegime?.missing_fields.join('、')}</small> : null}
          {(marketRegime?.notes ?? []).slice(0, 4).map(note => <small key={note}>· {note}</small>)}
        </div>}
      </section>

      <section className="panel reflexivity-panel">
        <header>
          <h3><Activity size={16} />预期拥挤与行为路径</h3>
          <span className="stream-state">匹配度不是概率 · 后续量价负责验证或证伪</span>
        </header>
        {consensusHighOpenFade
          ? <ConsensusHighOpenFadeCard signal={consensusHighOpenFade} />
          : <article className="consensus-fade-card consensus-fade-data-gap" aria-label="一致性高开兑现风险">
            <header><div><span className="consensus-fade-eyebrow">一致性高开兑现</span><strong>{loading ? '正在读取真实竞价与承接证据' : '证据不足，无法判断风险'}</strong><p>未取得可追溯的前一交易日深水修复、当日行业高开广度和开盘承接数据，不生成高开兑现结论。</p></div></header>
          </article>}
        {marketReflexivity ? <>
          <div className={`reflexivity-current ${reflexivityTone(marketReflexivity.current_scenario)}`}>
            <div><span>当前最匹配路径</span><strong>{marketReflexivity.current_scenario_label}</strong></div>
            <div><span>拥挤代理</span><strong>{marketReflexivity.crowding.label} · {marketReflexivity.crowding.score.toFixed(0)}分</strong></div>
            <div><span>规则匹配 / 置信度</span><strong>{marketReflexivity.scenario_match_score?.toFixed(0) ?? '--'} / {(marketReflexivity.confidence * 100).toFixed(0)}%</strong></div>
          </div>
          <div className="reflexivity-scenarios">
            {(marketReflexivity.scenarios ?? []).map(scenario => (
              <article key={scenario.code} className={scenario.code === marketReflexivity.current_scenario ? `active ${reflexivityTone(scenario.code)}` : ''}>
                <header><b>{scenario.label}</b><span>匹配 {scenario.match_score.toFixed(0)}</span></header>
                <p>{scenario.evidence?.[0] || `待补证据：${(marketReflexivity.missing_fields ?? []).join('、') || '下一快照'}`}</p>
                <small><b>下一验证：</b>{scenario.next_validation_points?.[0] || '等待下一份量价快照'}</small>
                <details><summary>查看证据、反证与纪律</summary>
                  {(scenario.evidence ?? []).slice(0, 3).map(item => <p key={`e-${item}`}>+ {item}</p>)}
                  {(scenario.counter_evidence ?? []).slice(0, 2).map(item => <p key={`c-${item}`}>- {item}</p>)}
                  {(scenario.allowed_actions ?? []).slice(0, 2).map(item => <p key={`a-${item}`}>允许：{item}</p>)}
                  {(scenario.forbidden_actions ?? []).slice(0, 2).map(item => <p className="risk-text" key={`f-${item}`}>禁止：{item}</p>)}
                </details>
              </article>
            ))}
          </div>
          <p className="reflexivity-method">{marketReflexivity.methodology_note}</p>
        </> : <p className="plain-text">正在用全A广度、指数分时均价、资金流和板块扩散计算可证伪路径。</p>}
      </section>

      <section className="panel global-evidence-panel">
        <header>
          <h3><Activity size={16} />外围市场证据</h3>
          <span className="stream-state">{globalCues?.data_quality === 'ok' ? '数据可用' : globalCues ? '部分数据降级' : '等待同步'} · {globalCues?.as_of ? new Date(globalCues.as_of).toLocaleString('zh-CN') : '--'}</span>
        </header>
        <p className="global-evidence-summary">{globalEvidenceSummary(globalCues)}</p>
        <div className="global-cue-groups">
          <div>
            <h4>韩国与半导体风向</h4>
            {[...(globalCues?.korea_indices ?? []), ...(globalCues?.korea_equities ?? [])].map(item => (
              <article key={`${item.market}-${item.symbol}`} className={globalQuoteTone(item.change_pct, item.status)}>
                <b>{item.name}</b><strong>{formatSignedNumber(item.change_pct, '%')}</strong>
                <small>{item.status === 'unavailable' ? item.note || '授权行情不可用' : `${formatNumber(item.price)} · ${item.freshness}`}</small>
              </article>
            ))}
            {!(globalCues?.korea_indices ?? []).length && !(globalCues?.korea_equities ?? []).length && <p>韩国行情未返回，不用其他指数替代。</p>}
          </div>
          <div>
            <h4>隔夜美股指数</h4>
            {(globalCues?.us_indices ?? []).map(item => (
              <article key={`${item.market}-${item.symbol}`} className={globalQuoteTone(item.change_pct, item.status)}>
                <b>{item.name}</b><strong>{formatSignedNumber(item.change_pct, '%')}</strong>
                <small>{item.freshness} · {item.source}</small>
              </article>
            ))}
            {!(globalCues?.us_indices ?? []).length && <p>隔夜美股指数暂不可用。</p>}
          </div>
          <div>
            <h4>隔夜美股行业表现</h4>
            {(globalCues?.us_sector_rank ?? []).slice(0, 8).map((item, index) => (
              <article key={item.symbol} className={globalQuoteTone(item.change_pct, item.status)}>
                <b>{index + 1}. {item.theme || item.name}</b><strong>{formatSignedNumber(item.change_pct, '%')}</strong>
                <small>{item.symbol} · {item.proxy_description || '行业ETF代理'}</small>
              </article>
            ))}
            {!(globalCues?.us_sector_rank ?? []).length && <p>美股行业ETF排行暂不可用，不生成模拟排名。</p>}
          </div>
        </div>
        {(globalCues?.notes ?? []).slice(0, 4).map(note => <small className="global-data-note" key={note}>· {note}</small>)}
      </section>

      <section className="panel opportunity-radar-panel">
        <header>
          <h3><Activity size={16} />盘中机会雷达</h3>
          <span className="stream-state">新闻假设 → 板块资金 → 相对强度 → 分时均价确认</span>
        </header>
        <p className="opportunity-discipline">{opportunityRadar?.discipline || '资讯不得单独触发买入，等待真实板块与个股量价确认。'}</p>
        <div className="intraday-expansion-block">
          <header>
            <h4>盘中增量方向</h4>
            <small>最近 {opportunityRadar?.intraday_expansion?.window_minutes || 15} 个交易分钟 · 新增涨停 + 资金拐点 + 板块强度</small>
          </header>
          <div className="opportunity-grid">
            {(intradayExpansion?.items ?? []).slice(0, 6).map(item => (
              <article key={`${item.sector}-${item.as_of}`} className={`opportunity-${opportunityTone(item.status)}`}>
                <header><span>{item.status}</span><b>{item.confirmation_score}分</b></header>
                <strong>{item.sector}</strong>
                <small>
                  近{item.window_minutes}分钟新增涨停 {item.new_limit_up_count} 只 · 当前共 {item.total_limit_up_count} 只 · 最高 {item.highest_board} 板
                </small>
                <p>{item.evidence?.[0] || item.counter_evidence?.[0] || `待补：${(item.missing ?? []).join('、')}`}</p>
                <p className="expansion-flow-line">
                  板块 {formatSignedNumber(item.change_pct, '%')} · 资金 {formatSignedNumber(item.net_inflow, '亿')}
                  {item.flow_speed !== null ? ` · 流速 ${formatSignedNumber(item.flow_speed, '亿/分钟')}` : ''}
                </p>
                <FlowKineticsEvidence fields={{
                  flow_speed: item.flow_speed,
                  flow_acceleration: item.flow_acceleration,
                  flow_turning: item.flow_turning,
                  flow_as_of: item.as_of,
                  flow_window_minutes: item.window_minutes,
                  flow_kinetics_reliable: item.flow_speed !== null,
                }} compact label="增量资金" />
                {item.leaders?.length ? <small>新增涨停/前排：{item.leaders.slice(0, 6).join('、')}</small> : null}
                <p className="opportunity-action">{item.action}</p>
                <details>
                  <summary>查看证据、风险与失效条件</summary>
                  {(item.evidence ?? []).slice(0, 3).map(text => <p key={`e-${text}`}>证据：{chineseEvidence(text)}</p>)}
                  {(item.counter_evidence ?? []).slice(0, 2).map(text => <p key={`c-${text}`}>反证：{chineseEvidence(text)}</p>)}
                  {(item.risk ?? []).slice(0, 2).map(text => <p key={text}>风险：{text}</p>)}
                  {(item.invalidation ?? []).slice(0, 3).map(text => <p key={text}>失效：{text}</p>)}
                </details>
              </article>
            ))}
          </div>
          {!(intradayExpansion?.items ?? []).length && (expansionUnavailable
            ? <div className="data-gap-state"><b>盘中增量证据不足，无法判断</b><p>{intradayExpansion?.notes?.[0] || (opportunityRadar ? '真实涨停梯队或板块资金时点尚未齐备，不以旧数据推断当前机会。' : '盘中机会雷达尚未同步成功，请稍后重试或手动刷新。')}</p></div>
            : <p className="plain-text">真实涨停梯队与资金数据可用，但当前未发现共同确认的增量方向；不生成模拟机会。</p>)}
          {intradayExpansion && <small className="global-data-note">数据质量：{expansionDataQualityLabel(intradayExpansion.data_quality)} · 证据时点：{formatConsensusAsOf(intradayExpansion.as_of)}</small>}
        </div>
        <div className="opportunity-grid">
          {(opportunityRadar?.items ?? []).slice(0, 8).map(item => (
            <article key={item.id} className={`opportunity-${opportunityTone(item.status)}`}>
              <header><span>{item.status}</span><b>{item.confirmation_score}分</b></header>
              {item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a> : <strong>{item.title}</strong>}
              <small>{item.primary_sector || (item.sectors ?? []).join('、') || '待映射板块'} · {item.source} · {formatAge(item.age_minutes)}</small>
              <small className="news-verification-line">
                {newsClaimLabel(item.claim_level)} · {newsValidationLabel(item.market_validation)} · {item.sentiment || '待验证'}
              </small>
              <p>{item.evidence?.[0] || item.counter_evidence?.[0] || `待补：${(item.missing ?? []).join('、') || '板块确认数据'}`}</p>
              <p className="opportunity-action">{item.action}</p>
            </article>
          ))}
        </div>
        {opportunityRadar && !opportunityRadar.items.length && <p className="plain-text">{opportunityRadar.data_quality === 'missing' ? '消息与市场验证数据不足，无法判断盘中消息机会。' : '真实消息与市场验证数据可用，但暂无共同确认的盘中消息机会。'}系统不会用空数据生成题材建议。</p>}
        {!opportunityRadar && <div className="data-gap-state"><b>机会雷达暂不可用</b><p>未取得可追溯的新闻、板块资金与相对强度数据，不生成题材建议。</p></div>}
      </section>

      <section className="panel holding-cockpit">
        <header>
          <h3><ListTodo size={16} />持仓预期与盘中证据驾驶舱</h3>
          <div className="holding-cockpit-actions">
            <span className="stream-state">选择持仓查看完整决策链</span>
            {selectedHolding && <PositionAiAssistant code={selectedHolding.code} name={selectedHolding.name} />}
          </div>
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
                <p><b>当前价 / 分时均价：</b>{formatPositivePrice(selectedCard.volume_price.price)} / {selectedCard.volume_price.vwap_reliable ? formatPositivePrice(selectedCard.volume_price.vwap) : '--'}</p>
                <p><b>量价状态：</b>{chineseLabel(selectedCard.volume_price.pattern)}</p>
                <p><b>高点回撤：</b>{Number.isFinite(selectedCard.volume_price.high_drawdown) ? `${selectedCard.volume_price.high_drawdown.toFixed(2)}%` : '--'}</p>
                <p><b>量比 / 上攻效率：</b>{selectedCard.volume_price.vwap_reliable ? `${selectedCard.volume_price.volume_ratio.toFixed(2)} / ${selectedCard.volume_price.attack_efficiency.toFixed(2)}` : '-- / --'}</p>
                <small>{selectedCard.volume_price.vwap_reliable ? '真实分钟数据已确认' : '分钟数据不足，结论已降级'}</small>
              </> : <p>暂无可靠量价快照，不生成确定性结论。</p>}
            </article>
            <article>
              <h4>当前操作与风险边界</h4>
              {selectedExecution ? <>
                <p><b>状态：</b>{chineseLabel(selectedExecution.state)}</p>
                <p><b>建议：</b>{chineseEvidence(selectedExecution.recommended_action)}</p>
                <p><b>建议仓位：</b><span className="private-value">{(selectedExecution.recommended_position_ratio * 100).toFixed(0)}% · 可卖 {selectedExecution.sellable_quantity} 股</span></p>
                <p><b>结构 / 硬止损：</b><span className="private-value">{selectedExecution.structure_stop_price.toFixed(2)} / {selectedExecution.hard_stop_price.toFixed(2)}</span></p>
                <small className="sensitive-evidence">{selectedExecution.invalid_conditions[0] || '等待失效条件确认'}</small>
              </> : <p>暂无持仓执行状态。</p>}
            </article>
            {selectedExecution && [
              selectedExecution.high_sell_signal,
              selectedExecution.panic_sell_guard,
              selectedExecution.contrarian_add_signal,
            ].filter((signal): signal is HoldingExecutionSignal => Boolean(signal)).map(signal => (
              <article key={signal.code} className={`cockpit-action-signal ${holdingSignalTone(signal)}`}>
                <header><h4>{signal.title}</h4><strong>{holdingSignalStatus(signal.status)}</strong></header>
                <p className="cockpit-signal-action">{signal.action}</p>
                {signal.recommended_ratio > 0 && <p><b>建议分批比例：</b>{(signal.recommended_ratio * 100).toFixed(0)}%</p>}
                {signal.evidence.slice(0, 3).map(item => <p className="sensitive-evidence" key={item}>+ {chineseEvidence(item)}</p>)}
                {signal.missing_conditions.slice(0, 2).map(item => <p className="cockpit-signal-missing" key={item}>待满足：{chineseEvidence(item)}</p>)}
                <details><summary>查看撤销与恢复条件</summary>
                  {signal.cancel_conditions.slice(0, 3).map(item => <p key={item}>撤销/升级：{chineseEvidence(item)}</p>)}
                  {signal.recovery_conditions.slice(0, 2).map(item => <p key={item}>后续复核：{chineseEvidence(item)}</p>)}
                </details>
              </article>
            ))}
            <article className={`cockpit-reflexivity-card ${selectedHolding && stockReflexivity[selectedHolding.code] ? reflexivityTone(stockReflexivity[selectedHolding.code].current_scenario) : ''}`}>
              <h4>个股预期拥挤与行为路径</h4>
              {selectedHolding && stockReflexivity[selectedHolding.code] ? (() => {
                const assessment = stockReflexivity[selectedHolding.code]
                return <>
                  <div className="stock-reflexivity-summary">
                    <p><b>当前路径：</b>{assessment.current_scenario_label}</p>
                    <p><b>拥挤代理：</b>{assessment.crowding.label} · {assessment.crowding.score.toFixed(0)}分</p>
                    <p><b>规则匹配：</b>{assessment.scenario_match_score?.toFixed(0) ?? '--'} · 置信度 {(assessment.confidence * 100).toFixed(0)}%</p>
                    <p><b>扩仓闸门：</b>{assessment.market_gate?.new_position_allowed ? '开放，仍需个股确认' : '关闭，禁止新增风险'}</p>
                  </div>
                  <div className="stock-reflexivity-evidence">
                    <div><b>当前证据</b>{(assessment.current_evidence ?? []).slice(0, 3).map(item => <p className="sensitive-evidence" key={item}>+ {item}</p>)}</div>
                    <div><b>反向证据</b>{(assessment.current_counter_evidence ?? []).slice(0, 3).map(item => <p className="sensitive-evidence" key={item}>- {item}</p>)}</div>
                    <div><b>下一验证</b>{(assessment.next_validation_points ?? []).slice(0, 3).map(item => <p key={item}>· {item}</p>)}</div>
                  </div>
                  <div className="stock-reflexivity-discipline">
                    <p><b>允许：</b>{(assessment.allowed_actions ?? []).slice(0, 2).join('；') || '等待证据补齐'}</p>
                    <p className="risk-text"><b>禁止：</b>{(assessment.forbidden_actions ?? []).slice(0, 2).join('；')}</p>
                  </div>
                </>
              })() : <p>正在读取该持仓的预期差、分时均价、日内承接和市场闸门。</p>}
            </article>
            <article className="cockpit-evidence-card">
              <h4>证据、反向证据与恢复条件</h4>
              <div><b>支持证据</b>{(selectedExecution?.evidence ?? selectedCard?.evidence ?? []).slice(0, 3).map(item => <p className="sensitive-evidence" key={item}>+ {chineseEvidence(item)}</p>)}</div>
              <div><b>反向证据</b>{(selectedExecution?.counter_evidence ?? selectedCard?.counter_evidence ?? []).slice(0, 3).map(item => <p className="sensitive-evidence" key={item}>- {chineseEvidence(item)}</p>)}</div>
              <div><b>恢复条件</b>{(selectedExecution?.recovery_conditions ?? []).slice(0, 3).map(item => <p className="sensitive-evidence" key={item}>· {chineseEvidence(item)}</p>)}</div>
            </article>
            <article className="cockpit-timeline-card">
              <h4>盘中事件时间线</h4>
              <div className="cockpit-timeline">
                {(selectedReview?.timeline ?? selectedCard?.timeline ?? []).slice(0, 8).map(event => (
                  <div className={intradayEventSemantics(event.event_type, event.severity).toneClass} key={`${event.id}-${event.captured_at}`}><time>{formatEventTime(event.captured_at)}</time><b>{chineseLabel(event.event_type)}</b><span className="sensitive-evidence">{chineseEvidence(event.evidence?.[0] || chineseLabel(event.severity))}</span></div>
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
              <div className={`trajectory-summary-latest ${review?.timeline[0] ? intradayEventSemantics(review.timeline[0].event_type, review.timeline[0].severity).toneClass : ''}`}>
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
      const activeCodes = new Set(nextHoldings.map(item => item.code))
      setIntradayReviews(previous => {
        const next = Object.fromEntries(Object.entries(previous).filter(([code]) => activeCodes.has(code)))
        results.forEach(result => {
          if (result.status === 'fulfilled') {
            const [code, review] = result.value
            next[code] = review as IntradayReview
          }
        })
        return next
      })
    })
  }

  function loadDecisionCards(nextHoldings: HoldingOut[]) {
    Promise.allSettled(nextHoldings.map(item =>
      fetchJsonWithTimeout(`${API_BASE}/api/stocks/${item.code}/decision-card`, 8000).then(card => [item.code, card] as const),
    )).then(results => {
      const activeCodes = new Set(nextHoldings.map(item => item.code))
      setDecisionCards(previous => {
        const next = Object.fromEntries(Object.entries(previous).filter(([code]) => activeCodes.has(code)))
        results.forEach(result => { if (result.status === 'fulfilled') next[result.value[0]] = result.value[1] })
        return next
      })
    })
  }

  function loadSingleIntradayReview(code: string) {
    fetchJsonWithTimeout(`${API_BASE}/api/stocks/${code}/intraday-review`, 15000)
      .then(review => {
        setIntradayReviews(prev => ({ ...prev, [code]: review as IntradayReview }))
      })
      .catch(() => undefined)
  }

  function loadStockReflexivity(code: string, forceRefresh: boolean) {
    const refreshQuery = forceRefresh ? '?force_refresh=true' : ''
    fetchJsonWithTimeout(`${API_BASE}/api/stocks/${code}/reflexivity${refreshQuery}`, 45000)
      .then(value => {
        stockReflexivityLoadedAt.current[code] = Date.now()
        setStockReflexivity(previous => ({ ...previous, [code]: value as ReflexivityAssessment }))
      })
      .catch(() => undefined)
  }

  function refreshHoldingDecision(code: string) {
    const now = Date.now()
    if (now - (holdingDecisionRefreshedAt.current[code] ?? 0) < 10_000) return
    holdingDecisionRefreshedAt.current[code] = now
    loadSingleIntradayReview(code)
    loadStockReflexivity(code, true)
    fetchJsonWithTimeout(`${API_BASE}/api/stocks/${code}/decision-card`, 12000)
      .then(value => setDecisionCards(previous => ({ ...previous, [code]: value as StockDecisionCard })))
      .catch(() => undefined)
    fetchJsonWithTimeout(`${API_BASE}/api/holdings/execution-states`, 12000)
      .then(value => {
        if (Array.isArray(value)) setExecutionStates(value as PositionExecutionState[])
      })
      .catch(() => undefined)
  }
}

function fetchJsonWithTimeout(url: string, timeoutMs = 5000) {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  return fetch(url, { signal: controller.signal })
    .then(async response => {
      if (!response.ok) {
        throw new Error(`请求失败：HTTP ${response.status}`)
      }
      return response.json()
    })
    .finally(() => window.clearTimeout(timeout))
}

function formatEventTime(value: string) {
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return value
  return time.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function formatNumber(value?: number | null, suffix = '') {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--'
  return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${suffix}`
}

function formatSignedNumber(value?: number | null, suffix = '') {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--'
  const prefix = value > 0 ? '+' : ''
  return `${prefix}${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${suffix}`
}

function formatRatio(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--'
  return `${(value * 100).toFixed(1)}%`
}

function formatPositivePrice(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(value) || value <= 0) return '--'
  return value.toFixed(2)
}

function expansionDataQualityLabel(value?: string | null) {
  const labels: Record<string, string> = {
    ok: '真实数据可用',
    degraded: '部分证据降级',
    missing: '关键证据缺失',
  }
  return labels[String(value || '').toLowerCase()] || '状态待确认'
}

function formatAge(value?: number | null) {
  if (value === null || value === undefined) return '时间待确认'
  if (value < 60) return `${value}分钟前`
  return `${Math.floor(value / 60)}小时${value % 60 ? `${value % 60}分` : ''}前`
}

function marketEffectLabel(score: number) {
  if (score >= 75) return '很强'
  if (score >= 60) return '较强'
  if (score >= 45) return '局部分化'
  if (score >= 30) return '偏弱'
  return '极弱'
}

function marketLossLabel(score: number) {
  if (score >= 75) return '极高'
  if (score >= 60) return '很高'
  if (score >= 45) return '偏高'
  if (score >= 30) return '一般'
  return '较低'
}

function globalQuoteTone(change?: number | null, status?: string) {
  if (status === 'unavailable' || status === 'configuration_pending') return 'cue-unavailable'
  if ((change ?? 0) <= -3) return 'cue-critical'
  if ((change ?? 0) < 0) return 'cue-negative'
  if ((change ?? 0) > 0) return 'cue-positive'
  return ''
}

function globalEvidenceSummary(cues: GlobalMarketCues | null) {
  if (!cues) return '正在读取韩国、隔夜美股及行业ETF代理数据；缺失数据不会以零值代替。'
  const negativeKorea = [...(cues.korea_indices ?? []), ...(cues.korea_equities ?? [])]
    .filter(item => item.change_pct !== null && item.change_pct <= -3)
  const negativeSemis = (cues.us_sector_rank ?? [])
    .filter(item => /半导体|SMH|SOXX/i.test(`${item.theme || ''}${item.symbol}`) && (item.change_pct ?? 0) < 0)
  if (negativeKorea.length) {
    return `韩国市场出现显著负反馈：${negativeKorea.map(item => `${item.name} ${formatSignedNumber(item.change_pct, '%')}`).join('、')}。涉及半导体、存储或科技持仓时提高开仓门槛，外围证据只作加减分，不单独触发交易。`
  }
  if (negativeSemis.length) {
    return `隔夜半导体代理走弱：${negativeSemis.map(item => `${item.symbol} ${formatSignedNumber(item.change_pct, '%')}`).join('、')}。需等待A股板块资金和个股VWAP独立确认。`
  }
  return '外围证据未出现明确系统性冲击；仍以A股全市场、板块资金和个股量价为主，外围只作当日预期修正。'
}

function opportunityTone(status: string) {
  if (status === '已确认' || status === '增量已确认') return 'confirmed'
  if (status === '证伪') return 'invalidated'
  if (status === '衰减') return 'decayed'
  return 'pending'
}

function newsClaimLabel(value: string) {
  const labels: Record<string, string> = {
    OFFICIAL: '正式公告',
    MEDIA_ATTRIBUTION: '媒体归因',
    RUMOR: '传闻待核验',
  }
  return labels[value] || '来源待核验'
}

function newsValidationLabel(value: string) {
  const labels: Record<string, string> = {
    CONFIRMED: '市场影响已验证',
    INVALIDATED: '市场影响已证伪',
    MIXED: '市场反馈分歧',
    DATA_GAP: '量价证据不足',
    PENDING: '等待市场验证',
  }
  return labels[value] || '等待市场验证'
}

function reflexivityTone(scenario: string) {
  if (scenario === 'DATA_GAP') return 'reflexivity-missing'
  if (['NO_REBOUND_LIQUIDATION', 'REBOUND_FAILURE_SUPPLY'].includes(scenario)) return 'reflexivity-risk'
  if (scenario === 'UPSIDE_SURPRISE_REPAIR') return 'reflexivity-positive'
  if (scenario === 'REBOUND_ABSORPTION') return 'reflexivity-watch'
  return ''
}

function isRiskEvent(event: IntradayEvidenceEvent) {
  return isActionableIntradayEvent(event.event_type, event.severity) || [
    'EXPECTATION_INVALIDATED', 'EXPECTATION_VOLUME_BREAKDOWN', 'VWAP_BROKEN',
    'VOLUME_PRICE_WEAKENING', 'HIGH_DRAWDOWN', 'PROFIT_DRAWDOWN_WARNING',
    'TIME_STOP_TRIGGERED', 'SECTOR_FLOW_PEAK_REVERSAL', 'PROFIT_TO_LOSS_RISK',
    'HIGH_SELL_WINDOW', 'PANIC_SELL_GUARD', 'CONTRARIAN_ADD_EVALUATION',
  ].includes(event.event_type)
}

function riskActionForEvent(event: IntradayEvidenceEvent, states: PositionExecutionState[]) {
  const state = states.find(item => item.code === event.target_code)
  const semantics = intradayEventSemantics(event.event_type, event.severity)
  if (semantics.kind === 'opportunity' || semantics.kind === 'watch') {
    const hardRisk = state && ['EXIT_REQUIRED', 'REDUCE_REQUIRED', 'EXPECTATION_INVALIDATED'].includes(state.state)
    return chineseEvidence(hardRisk
      ? `${semantics.guidance} 当前执行闸门仍要求：${state.recommended_action}，正向事件不自动解除硬风险。`
      : semantics.guidance)
  }
  return chineseEvidence(state?.recommended_action || semantics.guidance || (event.severity === 'critical' ? '立即降低风险' : '核对并按计划处理'))
}

function riskDetailForEvent(
  event: IntradayEvidenceEvent,
  cards: Record<string, StockDecisionCard>,
  states: PositionExecutionState[],
) {
  const card = cards[event.target_code]
  const state = states.find(item => item.code === event.target_code)
  const base = chineseEvidence(event.evidence?.[0] || `${chineseLabel(event.severity)} / 优先级 ${event.priority}`)
  if (!card || !['INVALID', 'WEAKER'].includes(card.expectation.expectation_result)) return base
  return `合理开盘 ${card.expectation.expected_open_low.toFixed(2)}%～${card.expectation.expected_open_high.toFixed(2)}%，实际 ${card.expectation.actual_open_pct >= 0 ? '+' : ''}${card.expectation.actual_open_pct.toFixed(2)}%，预期差 ${card.expectation.expectation_gap_score}；${base}；建议：${chineseEvidence(state?.recommended_action || card.expectation.suggestion)}`
}

function riskTone(value?: string) {
  if (!value) return ''
  if (/EXIT|CRITICAL|HIGH|高|INVALID/.test(value)) return 'risk-high'
  if (/REDUCE|WARNING|中高|中|PROTECT/.test(value)) return 'risk-medium'
  if (/WATCH|观察|LOW|低/.test(value)) return 'risk-low'
  return ''
}

function holdingSignalTone(signal: HoldingExecutionSignal) {
  if (signal.code === 'HIGH_SELL_WINDOW' && signal.status === 'ACTIVE') return signal.level === 'HIGH' ? 'signal-sell-high' : 'signal-sell-medium'
  if (signal.code === 'PANIC_SELL_GUARD' && signal.status === 'ACTIVE') return 'signal-panic-guard'
  if (signal.code === 'CONTRARIAN_ADD_EVALUATION' && signal.status === 'ELIGIBLE') return 'signal-add-eligible'
  if (signal.status === 'BLOCKED') return 'signal-blocked'
  if (signal.status === 'EXPIRED') return 'signal-expired'
  return 'signal-neutral'
}

function holdingSignalStatus(status: string) {
  const labels: Record<string, string> = {
    ACTIVE: '立即关注',
    WATCH: '等待确认',
    EXPIRED: '窗口已过',
    ELIGIBLE: '仅允许评估',
    BLOCKED: '禁止执行',
    INACTIVE: '未触发',
  }
  return labels[status] || chineseLabel(status)
}

function DecisionBasis({ execution, fallback = [] }: { execution?: PositionExecutionState; fallback?: string[] }) {
  const evidence = execution?.evidence?.length ? execution.evidence : fallback
  return <div className="decision-basis">
    {evidence.slice(0, 6).map(item => <p className="sensitive-evidence" key={item}>依据：{chineseEvidence(item)}</p>)}
    {(execution?.invalid_conditions ?? []).slice(0, 3).map(item => <p className="sensitive-evidence" key={item}>升级/退出条件：{chineseEvidence(item)}</p>)}
    {(execution?.recovery_conditions ?? []).slice(0, 3).map(item => <p className="sensitive-evidence" key={item}>保留/恢复条件：{chineseEvidence(item)}</p>)}
    {!evidence.length && <p>等待下一次量价和执行状态采样补充依据。</p>}
  </div>
}

function ConsensusHighOpenFadeCard({ signal }: { signal: ConsensusHighOpenFade }) {
  const view = buildConsensusHighOpenFadeView(signal)
  const evidence = signal.evidence ?? []
  const counterEvidence = signal.counter_evidence ?? []
  const missingFields = signal.missing_fields ?? []
  const forbiddenActions = signal.forbidden_actions ?? []
  const sources = signal.source ?? []
  const asOf = formatConsensusAsOf(signal.as_of)
  const riskLabel = view.state === 'triggered-high'
    ? '高风险'
    : view.state === 'triggered-medium'
      ? '中风险'
      : view.state === 'data-gap'
        ? '风险无法判断'
        : '风险尚未确认'

  return (
    <article className={`consensus-fade-card ${view.toneClass}`} aria-label="一致性高开兑现风险">
      <header>
        <div>
          <span className="consensus-fade-eyebrow">一致性高开兑现</span>
          <strong>{view.statusLabel}</strong>
          <p>{view.conclusion}</p>
        </div>
        <div className="consensus-fade-score">
          <span>规则分数</span>
          <strong>{view.scoreLabel}</strong>
          <small>{riskLabel}</small>
        </div>
      </header>

      <div className="consensus-fade-details">
        <section>
          <h4>支持与反向证据</h4>
          {evidence.length
            ? evidence.slice(0, 5).map(item => <p key={`fade-evidence-${item}`}>+ {item}</p>)
            : <p>暂无已确认支持证据。</p>}
          {counterEvidence.slice(0, 3).map(item => <p key={`fade-counter-${item}`}>− {item}</p>)}
        </section>
        <section className={missingFields.length ? 'consensus-fade-missing' : ''}>
          <h4>缺失字段</h4>
          <p>{missingFields.length ? missingFields.join('、') : '无已报告缺失字段'}</p>
        </section>
        <section className={forbiddenActions.length && view.riskColored ? 'consensus-fade-forbidden' : ''}>
          <h4>禁止动作</h4>
          {forbiddenActions.length
            ? forbiddenActions.slice(0, 5).map(item => <p key={`fade-forbidden-${item}`}>禁止：{item}</p>)
            : <p>暂无新增禁止动作，仍须等待后续量价验证。</p>}
        </section>
        <section>
          <h4>数据来源与时点</h4>
          <p>{sources.length ? sources.join('、') : '暂无可追溯数据源'}</p>
          <p>证据时点：{asOf} · 交易日：{signal.trade_date || '--'}</p>
        </section>
      </div>

      {(signal.next_validation_points ?? []).length > 0 && (
        <p className="consensus-fade-next"><b>下一验证：</b>{signal.next_validation_points.slice(0, 3).join('；')}</p>
      )}
      <small className="consensus-fade-method">{signal.methodology_note}</small>
    </article>
  )
}

function formatConsensusAsOf(value: string | null) {
  if (!value) return '--'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN')
}

function inferMarketCycle(temperature?: string, marketMode?: string) {
  const text = `${temperature || ''}${marketMode || ''}`
  if (/退潮|冰点|极弱|防守/.test(text)) return '退潮防守'
  if (/高潮|过热|加速/.test(text)) return '高潮分歧'
  if (/强|活跃|主升/.test(text)) return '主升活跃'
  return '轮动分歧'
}

function isMarketSession(includeClosingGrace = false) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date())
  const value = Object.fromEntries(parts.map(part => [part.type, part.value]))
  const weekday = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].includes(value.weekday || '')
  const minutes = Number(value.hour || 0) * 60 + Number(value.minute || 0)
  const closeMinute = 15 * 60 + (includeClosingGrace ? 5 : 0)
  return weekday && minutes >= 9 * 60 + 15 && minutes <= closeMinute
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
