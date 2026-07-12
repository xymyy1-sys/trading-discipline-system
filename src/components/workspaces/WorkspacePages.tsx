import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  CheckCircle2,
  Clock3,
  ListTodo,
  RefreshCcw,
} from 'lucide-react'
import { API_BASE } from '../../api'

import type { HoldingOut, IntradayEvidenceEvent, MarketSeesaw, PositionExecutionState, ThemeRadar } from '../../types'

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
  const ActiveComponent = selected?.Component

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
        {ActiveComponent ? <ActiveComponent /> : null}
      </section>
    </section>
  )
}

export function TodayDecisionSummary() {
  const [holdings, setHoldings] = useState<HoldingOut[]>([])
  const [executionStates, setExecutionStates] = useState<PositionExecutionState[]>([])
  const [realtimeEvents, setRealtimeEvents] = useState<IntradayEvidenceEvent[]>([])
  const [streamState, setStreamState] = useState('连接中')
  const [seesaw, setSeesaw] = useState<MarketSeesaw | null>(null)
  const [theme, setTheme] = useState<ThemeRadar | null>(null)
  const [loading, setLoading] = useState(false)

  const load = () => {
    setLoading(true)
    Promise.allSettled([
      fetchJsonWithTimeout(`${API_BASE}/api/holdings`),
      fetchJsonWithTimeout(`${API_BASE}/api/holdings/execution-states`),
      fetchJsonWithTimeout(`${API_BASE}/api/market/seesaw-monitor`, 8000),
      fetchJsonWithTimeout(`${API_BASE}/api/market/theme-radar`, 8000),
    ]).then(results => {
      const [holdingRes, executionRes, seesawRes, themeRes] = results
      if (holdingRes.status === 'fulfilled' && Array.isArray(holdingRes.value)) setHoldings(holdingRes.value)
      if (executionRes.status === 'fulfilled' && Array.isArray(executionRes.value)) setExecutionStates(executionRes.value)
      if (seesawRes.status === 'fulfilled') setSeesaw(seesawRes.value)
      if (themeRes.status === 'fulfilled') setTheme(themeRes.value)
    }).finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    const source = new EventSource(`${API_BASE}/api/intraday-events/stream`)
    source.onopen = () => setStreamState('实时推送已连接')
    source.onerror = () => setStreamState('实时推送重连中')
    source.addEventListener('intraday-risk', event => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as IntradayEvidenceEvent
        setRealtimeEvents(prev => [payload, ...prev.filter(item => item.id !== payload.id)].slice(0, 8))
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
        <strong>{theme?.market_temperature ?? '--'}</strong>
        <small>最强题材：{theme?.strongest_theme?.name ?? '--'}</small>
      </div>
      <div className="command-card">
        <span>资金迁移</span>
        <strong>{seesaw?.market_mode ?? '--'}</strong>
        <small>{seesaw?.summary ?? '等待资金跷跷板数据'}</small>
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
                <span>{item.recommended_action}</span>
                <small>{item.evidence[0] ?? item.volume_price_state}</small>
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
        {!seesaw?.inflow_targets?.length && <p className="plain-text">暂无资金迁移证据。</p>}
      </div>

      <div className="panel command-list realtime-events">
        <header>
          <h3><AlertTriangle size={16} />实时风险事件</h3>
          <span className="stream-state">{streamState}</span>
        </header>
        {realtimeEvents.length ? (
          realtimeEvents.map(event => (
            <article key={`${event.id}-${event.event_type}`}>
              <b>{event.target_name || event.target_code}</b>
              <span>{event.event_type}</span>
              <small>{event.evidence?.[0] ?? `${event.severity} / 优先级 ${event.priority}`}</small>
            </article>
          ))
        ) : (
          <p className="plain-text">暂无新推送事件；后台采集或手动刷新触发后会实时出现。</p>
        )}
      </div>
    </section>
  )
}

function fetchJsonWithTimeout(url: string, timeoutMs = 5000) {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  return fetch(url, { signal: controller.signal })
    .then(r => r.json())
    .finally(() => window.clearTimeout(timeout))
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
