import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type FormEvent,
  type ReactNode,
  type SetStateAction,
} from 'react'
import {
  AlertTriangle,
  BarChart3,
  Clock3,
  FlaskConical,
  History,
  RefreshCcw,
  Send,
  ShieldAlert,
  WalletCards,
  XCircle,
} from 'lucide-react'
import { API_BASE } from '../api'
import type {
  SimulationAccount,
  SimulationCalibrationProposal,
  SimulationDailyEquity,
  SimulationEvidence,
  SimulationFill,
  SimulationOrder,
  SimulationOrderSide,
  SimulationOrderType,
  SimulationPerformance,
  SimulationPerformanceSlice,
  SimulationPosition,
  SimulationShadowDecision,
  SimulationStrategyType,
  SimulationValidation,
} from '../types'

const ACTIVE_ACCOUNT_KEY = 'simulation-account-id'
const STRATEGY_LABELS: Record<SimulationStrategyType, string> = {
  limit_up: '打板策略',
  expectation_volume_price: '预期 × 量价策略',
  holding_execution: '持仓执行策略',
}
const STATUS_LABELS: Record<string, string> = {
  ACTIVE: '运行中', OPEN: '等待模拟撮合', PENDING: '待撮合', PARTIAL: '部分模拟成交',
  FILLED: '已模拟成交', CANCELLED: '已撤销', CANCELED: '已撤销', REJECTED: '已拒绝', EXPIRED: '已失效',
  ORDER_CREATED: '已生成影子委托', ORDER_REJECTED: '影子委托被拒绝', SKIPPED: '证据闸门跳过',
}
const REGIME_LABELS: Record<string, string> = {
  STRONG_EXPANSION: '强势扩张', REBOUND: '修复反弹', ROTATION: '轮动分歧',
  WEAK_CONTRACTION: '弱势收缩', PANIC: '恐慌释放', UNKNOWN: '市场环境未知',
}
const GAP_LABELS: Record<string, string> = {
  severe_negative: '严重负预期差', negative: '负预期差', matched: '符合预期',
  positive: '正预期差', strong_positive: '强正预期差', unknown: '预期差未知',
}

type OrderDraft = {
  code: string
  side: SimulationOrderSide
  order_type: SimulationOrderType
  price: string
  quantity: string
  strategy_source: SimulationStrategyType
  note: string
}
const emptyOrder: OrderDraft = {
  code: '', side: 'BUY', order_type: 'LIMIT', price: '', quantity: '100',
  strategy_source: 'expectation_volume_price', note: '',
}

async function simulationRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string | Array<{ msg?: string }> }
    const detail = Array.isArray(payload.detail) ? payload.detail.map(item => item.msg).filter(Boolean).join('；') : payload.detail
    throw new Error(detail || `模拟盘请求失败（HTTP ${response.status}）`)
  }
  return response.json() as Promise<T>
}

function money(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '--'
  return `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
function percent(value: number | null | undefined, signed = false) {
  if (value == null || !Number.isFinite(value)) return '--'
  return `${signed && value > 0 ? '+' : ''}${value.toFixed(2)}%`
}
function numberValue(value: number | null | undefined, digits = 2) {
  if (value == null || !Number.isFinite(value)) return '--'
  return value.toFixed(digits)
}
function displayTime(value?: string | null) {
  if (!value) return '--'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}
function statusLabel(status: string) { return STATUS_LABELS[status.toUpperCase()] || status || '未知' }
function statusTone(status: string) {
  const normalized = status.toUpperCase()
  if (['FILLED', 'ACTIVE'].includes(normalized)) return 'ok'
  if (['REJECTED', 'EXPIRED'].includes(normalized)) return 'danger'
  if (['CANCELLED', 'CANCELED'].includes(normalized)) return 'muted'
  return 'pending'
}
function strategyLabel(value: string) { return STRATEGY_LABELS[value as SimulationStrategyType] || value || '策略未标注' }

function useSimulationAccounts() {
  const [accounts, setAccounts] = useState<SimulationAccount[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [loadingAccounts, setLoadingAccounts] = useState(true)
  const [accountError, setAccountError] = useState('')
  const loadAccounts = useCallback(() => {
    setLoadingAccounts(true); setAccountError('')
    simulationRequest<SimulationAccount[]>('/api/simulation/accounts')
      .then(rows => {
        setAccounts(rows)
        const stored = Number(localStorage.getItem(ACTIVE_ACCOUNT_KEY))
        const selected = rows.find(item => item.id === stored) ?? rows[0] ?? null
        setActiveId(selected?.id ?? null)
        if (selected) localStorage.setItem(ACTIVE_ACCOUNT_KEY, String(selected.id))
      })
      .catch(value => setAccountError(value instanceof Error ? value.message : '模拟账户读取失败'))
      .finally(() => setLoadingAccounts(false))
  }, [])
  useEffect(() => {
    loadAccounts()
    const sync = () => loadAccounts()
    window.addEventListener('simulation-account-changed', sync)
    return () => window.removeEventListener('simulation-account-changed', sync)
  }, [loadAccounts])
  useEffect(() => {
    const syncSelection = (event: Event) => {
      const requested = Number((event as CustomEvent<number>).detail)
      if (accounts.some(item => item.id === requested)) setActiveId(requested)
    }
    window.addEventListener('simulation-account-selected', syncSelection)
    return () => window.removeEventListener('simulation-account-selected', syncSelection)
  }, [accounts])
  const selectAccount = (id: number) => {
    setActiveId(id)
    localStorage.setItem(ACTIVE_ACCOUNT_KEY, String(id))
    window.dispatchEvent(new CustomEvent('simulation-account-selected', { detail: id }))
  }
  return { accounts, activeId, selectAccount, loadAccounts, loadingAccounts, accountError }
}

function SimulationNotice({ dataAsOf }: { dataAsOf?: string }) {
  return <div className="simulation-notice" role="note"><FlaskConical size={20} /><div><strong>模拟盘 · 不连接券商 · 不会真实下单</strong><span>模拟撮合只用于验证策略和执行纪律；每笔委托都保留行情时点、证据快照和未成交/拒绝原因。</span></div><small>数据时点：{displayTime(dataAsOf)}</small></div>
}
function AccountPicker({ accounts, activeId, onSelect }: { accounts: SimulationAccount[]; activeId: number | null; onSelect: (id: number) => void }) {
  return <div className="simulation-account-picker"><label>当前模拟账户<select value={activeId ?? ''} onChange={event => onSelect(Number(event.target.value))} disabled={!accounts.length}><option value="">{accounts.length ? '选择账户' : '请先创建模拟账户'}</option>{accounts.map(account => <option key={account.id} value={account.id}>{account.account_type === 'shadow' ? '自动影子验证｜' : ''}{account.name}（#{account.id}）</option>)}</select></label><small>账户切换只影响模拟账本；自动影子账户每分钟按真实证据前向验证。</small></div>
}
function ModuleState({ loading, error, empty, onRefresh }: { loading: boolean; error: string; empty?: string; onRefresh: () => void }) {
  if (loading) return <div className="simulation-state"><RefreshCcw className="spin" size={18} /><span>正在读取模拟账本，不会刷新真实持仓。</span></div>
  if (error) return <div className="simulation-state is-error"><ShieldAlert size={18} /><span>{error}</span><button type="button" onClick={onRefresh}>重试</button></div>
  return <div className="simulation-state"><Clock3 size={18} /><span>{empty || '暂无模拟数据。'}</span></div>
}
function ModuleHeading({ title, subtitle, loading, onRefresh, extra }: { title: string; subtitle: string; loading: boolean; onRefresh: () => void; extra?: ReactNode }) {
  return <header className="simulation-module-heading"><div><h3>{title}</h3><p>{subtitle}</p></div><div className="simulation-heading-actions">{extra}<button className="refresh-btn inline" type="button" onClick={onRefresh} disabled={loading}><RefreshCcw size={15} />{loading ? '读取中' : '刷新模拟数据'}</button></div></header>
}

type AiTraderRunResult = {
  account_id: number
  evaluated_at: string
  created_order_ids: number[]
  skipped_count: number
  duplicate_count: number
  skipped: Array<{ code: string; reason: string }>
}

type AutonomousCandidate = {
  rank: number
  code: string
  name: string
  industry: string
  score: number
  style: string
  change_pct: number
  volume_ratio: number
  turnover_rate: number
  price_vs_vwap: number
  reasons: string[]
  risks: string[]
  next_plan: string
  source_tags: string[]
  source_contributions: Array<{ source: string; base: number; learned: number }>
}

type AutonomousSelection = {
  trade_date: string
  captured_at?: string
  total_scanned: number
  candidate_count: number
  scope_note: string
  method?: string
  gate: { allow_entry: boolean; reason: string; max_entries?: number }
  items: AutonomousCandidate[]
  exploration_items?: AutonomousCandidate[]
  exploration_policy?: { minimum_score: number; maximum_daily_entries: number; position_ratio: number; purpose: string }
  reference_sources?: Record<string, { status: string; matched_count: number }>
  source_feedback?: Record<string, { sample_count: number; mean_return_pct: number; score_adjustment: number }>
}

type IntradayCollectorStatus = {
  enabled: boolean
  interval_seconds: number
  running: boolean
  last_success_at: string | null
  last_error: string
  market_regime_running: boolean
  market_regime_interval_seconds: number
  market_regime_last_success_at: string | null
  market_regime_last_error: string
  opportunity_radar_running: boolean
  opportunity_radar_last_success_at: string | null
  opportunity_radar_last_error: string
  simulation_match_running: boolean
  simulation_match_last_success_at: string | null
  simulation_match_last_error: string
  simulation_shadow_running: boolean
  simulation_shadow_last_success_at: string | null
  simulation_shadow_last_error: string
  simulation_shadow_equity_last_success_at: string | null
  simulation_shadow_equity_last_error: string
  close_expectation_completed_date: string | null
  close_shadow_equity_completed_date: string | null
}

export function SimulationAiTrader() {
  const [account, setAccount] = useState<SimulationAccount | null>(null)
  const [positions, setPositions] = useState<SimulationPosition[]>([])
  const [orders, setOrders] = useState<SimulationOrder[]>([])
  const [equities, setEquities] = useState<SimulationDailyEquity[]>([])
  const [decisions, setDecisions] = useState<SimulationShadowDecision[]>([])
  const [performance, setPerformance] = useState<SimulationPerformance | null>(null)
  const [calibration, setCalibration] = useState<SimulationCalibrationProposal | null>(null)
  const [validation, setValidation] = useState<SimulationValidation | null>(null)
  const [collector, setCollector] = useState<IntradayCollectorStatus | null>(null)
  const [selection, setSelection] = useState<AutonomousSelection | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    simulationRequest<SimulationAccount>('/api/simulation/ai-trader/account', { method: 'POST' })
      .then(row => {
        setAccount(row)
        localStorage.setItem(ACTIVE_ACCOUNT_KEY, String(row.id))
        return Promise.all([
          simulationRequest<SimulationPosition[]>(`/api/simulation/accounts/${row.id}/positions`),
          simulationRequest<SimulationOrder[]>(`/api/simulation/accounts/${row.id}/orders?limit=200`),
          simulationRequest<SimulationDailyEquity[]>(`/api/simulation/accounts/${row.id}/equity?limit=120`),
          simulationRequest<SimulationShadowDecision[]>(`/api/simulation/accounts/${row.id}/shadow-decisions?limit=200`),
          simulationRequest<SimulationPerformance>(`/api/simulation/accounts/${row.id}/performance`),
          simulationRequest<SimulationCalibrationProposal>(`/api/simulation/accounts/${row.id}/calibration-proposal`).catch(() => null),
          simulationRequest<SimulationValidation>(`/api/simulation/accounts/${row.id}/validation`).catch(() => null),
          simulationRequest<IntradayCollectorStatus>('/api/intraday-collector/status'),
          simulationRequest<AutonomousSelection>('/api/simulation/ai-trader/candidates'),
        ])
      })
      .then(([positionRows, orderRows, equityRows, decisionRows, report, calibrationProposal, validationReport, collectorStatus, selectionRows]) => {
        setPositions(positionRows)
        setOrders(orderRows)
        setEquities(equityRows)
        setDecisions(decisionRows)
        setPerformance(report)
        setCalibration(calibrationProposal)
        setValidation(validationReport)
        setCollector(collectorStatus)
        setSelection(selectionRows)
      })
      .catch(value => setError(value instanceof Error ? value.message : 'AI模拟交易员账本读取失败'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => load(), [load])

  const runOnce = () => {
    setRunning(true)
    setMessage('正在按当前证据扫描一次虚拟交易机会……')
    simulationRequest<AiTraderRunResult>('/api/simulation/ai-trader/run', { method: 'POST' })
      .then(result => {
        const created = result.created_order_ids.length
        const skipped = result.skipped_count
        const sampleReason = result.skipped?.[0]?.reason
        setMessage(created ? `本次生成 ${created} 笔虚拟委托，重复信号 ${result.duplicate_count} 条。` : `本次没有生成新委托，跳过 ${skipped} 条。${sampleReason ? `首要原因：${sampleReason}` : ''}`)
        load()
      })
      .catch(value => setMessage(value instanceof Error ? value.message : 'AI模拟交易员运行失败'))
      .finally(() => setRunning(false))
  }

  const markEquity = () => {
    if (!account) return
    setMessage('正在用当前真实行情校准AI模拟账户权益……')
    simulationRequest<SimulationDailyEquity>(`/api/simulation/accounts/${account.id}/equity/mark`, { method: 'POST' })
      .then(row => { setMessage(`权益已校准：${displayTime(row.captured_at)}。`); load() })
      .catch(value => setMessage(value instanceof Error ? value.message : 'AI模拟账户权益校准失败'))
  }

  const latest = equities[0]
  const marketValue = latest?.market_value ?? positions.reduce((sum, item) => sum + item.market_value, 0)
  const totalEquity = latest?.total_equity ?? (account ? account.cash + marketValue : null)
  const tradeDate = latest?.trade_date || decisions[0]?.trade_date || orders[0]?.trade_date || ''
  const todayDecisions = decisions.filter(item => !tradeDate || item.trade_date === tradeDate)
  const todayOrders = orders.filter(item => !tradeDate || item.trade_date === tradeDate)
  const createdOrders = todayOrders.filter(item => item.client_note.includes('shadow:') || item.decision_evidence_snapshot_id)
  const skippedReasons = todayDecisions.filter(item => item.status.toUpperCase() === 'SKIPPED').slice(0, 6)
  const formalPerformance = performance?.formal
  const sampleEnough = (formalPerformance?.closed_trade_count ?? 0) >= 20
  const policy = sampleEnough
    ? `已有 ${formalPerformance?.closed_trade_count ?? 0} 笔正式策略闭环样本，后续重点看回撤是否收敛、盈亏比是否稳定。`
    : `当前正式策略闭环样本 ${formalPerformance?.closed_trade_count ?? 0} 笔，探索样本 ${performance?.exploration.closed_trade_count ?? 0} 笔；两者严格分账，探索结果不参与正式参数校准。`

  return <section className="simulation-page ai-trader-page">
    <SimulationNotice dataAsOf={latest?.captured_at || account?.updated_at} />
    <ModuleHeading
      title="AI模拟交易员 · 2万元前向实盘"
      subtitle="Codex只做虚拟买卖：后台定时获取数据、自动筛选与生成委托、记录跳过原因，并用每日盈亏反向校准策略。"
      loading={loading}
      onRefresh={load}
      extra={<>
        <button className="refresh-btn inline" type="button" onClick={runOnce} disabled={running || loading}><FlaskConical size={15} />{running ? '扫描中' : '备用调试：扫描一次'}</button>
        <button className="refresh-btn inline" type="button" onClick={markEquity} disabled={!account || loading}><BarChart3 size={15} />备用调试：校准权益</button>
      </>}
    />
    {message && <p className="simulation-form-message">{message}</p>}
    {error ? <ModuleState loading={loading} error={error} onRefresh={load} /> : <div className="ai-trader-grid">
      <section className="ai-trader-hero panel">
        <div>
          <span>专属虚拟账户</span>
          <h4>{account?.name || '正在创建AI模拟账户'}</h4>
          <p>虚拟本金固定为 {money(account?.initial_cash ?? 20000)}。不用你手动触发：后端按交易时段循环采集、撮合、决策和收盘复盘；这里不会连接券商，也不会读取或改动真实账户资金。</p>
        </div>
        <strong>{money(totalEquity)}</strong>
        <small>账本日：{tradeDate || '等待首个交易日'} · 账户 #{account?.id ?? '--'}</small>
      </section>
      <AiTraderAutomationPanel status={collector} />
      <section className="simulation-section panel">
        <div className="simulation-section-title">
          <div><h4>全市场多源选股与买入闸门</h4><small>{selection?.scope_note || '等待全A实时行情扫描。'}</small></div>
          <span>{selection?.total_scanned?.toLocaleString() || 0}只扫描 / {selection?.candidate_count || 0}只候选</span>
        </div>
        <p className={selection?.gate.allow_entry ? 'plain-text' : 'simulation-unfilled'}>
          <b>{selection?.gate.allow_entry ? '允许小仓验证' : '当前禁止新开仓'}：</b>{selection?.gate.reason || '等待市场状态证据'}
        </p>
        <div className="ai-trader-skip-list">
          {(selection?.items || []).slice(0, 12).map(item => <article key={item.code}>
            <b>#{item.rank} {item.name}（{item.code}）· {item.score.toFixed(1)}分</b>
            <span>{item.industry} · {item.style} · 涨幅{item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%</span>
            {!!item.source_tags?.length && <span>来源共振：{item.source_tags.join(' + ')}</span>}
            <p>{item.reasons.join('；')}</p>
            <small>量比{item.volume_ratio.toFixed(2)} · 换手{item.turnover_rate.toFixed(2)}% · 相对分时均价{item.price_vs_vwap > 0 ? '+' : ''}{item.price_vs_vwap.toFixed(2)}%{item.risks.length ? ` · 风险：${item.risks.join('；')}` : ''}</small>
          </article>)}
          {!selection?.items?.length && <p className="plain-text">尚无全市场候选。系统仍会扫描全A；抓涨停、断板反包等模块只提供辅助证据，不会为了成交而降低门槛。</p>}
        </div>
        <p className="plain-text">来源反馈：{Object.entries(selection?.source_feedback || {}).map(([name, value]) => `${name} ${value.sample_count}笔 / 均值${value.mean_return_pct >= 0 ? '+' : ''}${value.mean_return_pct.toFixed(2)}% / 调分${value.score_adjustment >= 0 ? '+' : ''}${value.score_adjustment.toFixed(1)}`).join('；') || '尚无足够闭环成交，暂不自动调整来源权重。'}</p>
        {selection?.exploration_policy && <p className="simulation-unfilled"><b>每日探索规则：</b>正常策略到10:00仍未成交时，最多选择1只数据完整且未出现负向量价结构的候选，以{(selection.exploration_policy.position_ratio * 100).toFixed(0)}%确认仓取得有统计意义的前向样本；正式策略按确信度使用进攻仓、确认仓或主攻仓，探索交易仍单独标记。</p>}
        <footer>{selection?.method || '候选仍需分钟量价确认；候选不等于买入，不为成交而降低纪律。'}</footer>
      </section>
      <div className="simulation-kpi-grid ai-trader-kpis">
        <SimulationMetric label="虚拟总资产" value={money(totalEquity)} detail={latest ? percent(latest.return_pct, true) : '等待收盘校准'} tone={(latest?.total_pnl ?? 0) >= 0 ? 'up' : 'down'} />
        <SimulationMetric label="可用资金" value={money(account?.cash)} />
        <SimulationMetric label="持仓市值" value={money(marketValue)} />
        <SimulationMetric label="今日盈亏" value={money(latest?.daily_pnl)} tone={(latest?.daily_pnl ?? 0) >= 0 ? 'up' : 'down'} />
        <SimulationMetric label="累计盈亏" value={money(latest?.total_pnl ?? (totalEquity == null || !account ? null : totalEquity - account.initial_cash))} tone={(latest?.total_pnl ?? 0) >= 0 ? 'up' : 'down'} />
        <SimulationMetric label="最大回撤" value={latest ? percent(Math.abs(latest.drawdown_pct)) : '--'} tone="down" />
        <SimulationMetric label="正式策略胜率" value={formalPerformance ? percent(formalPerformance.win_rate) : '--'} detail={`${formalPerformance?.closed_trade_count ?? 0}笔正式 / ${performance?.exploration.closed_trade_count ?? 0}笔探索`} />
        <SimulationMetric label="正式策略盈亏比" value={formalPerformance ? numberValue(formalPerformance.profit_loss_ratio) : '--'} />
      </div>
      <AiTraderFeedbackPanel decisions={todayDecisions} orders={todayOrders} performance={performance} calibration={calibration} />
      <AiTraderValidationPanel validation={validation} />
      <section className="simulation-section panel">
        <div className="simulation-section-title"><h4><History size={17} />今日AI操作</h4><span>{createdOrders.length}笔委托 / {todayDecisions.length}条信号</span></div>
        <div className="ai-trader-actions">
          {todayOrders.slice(0, 10).map(order => <article key={order.id} className={`tone-${statusTone(order.status)}`}>
            <div><b>{order.name || order.code}</b><small>{order.code} · {strategyLabel(order.strategy_source)}</small></div>
            <strong>{order.side === 'BUY' ? '虚拟买入' : '虚拟卖出'} {order.quantity}股</strong>
            <span>{statusLabel(order.status)} · {order.order_type === 'LIMIT' ? numberValue(order.limit_price) : '市价撮合'}</span>
            <p>{order.reject_reason || order.client_note.replace(/^shadow:[^;]+;rule=[^;]+;reason=/, '') || '等待撮合器给出成交或拒绝原因。'}</p>
            <small>{displayTime(order.submitted_at)}</small>
          </article>)}
          {!todayOrders.length && <p className="plain-text">今日还没有虚拟委托。正常情况下不需要手动触发；若处于交易时段但仍无委托，通常说明证据闸门没有放行，AI交易员选择空仓等待。</p>}
        </div>
      </section>
      <section className="simulation-section panel">
        <div className="simulation-section-title"><h4><ShieldAlert size={17} />为什么没有交易 / 跳过原因</h4><span>{skippedReasons.length}条</span></div>
        <div className="ai-trader-skip-list">
          {skippedReasons.map(item => <article key={item.id}><b>{item.name || item.code}</b><span>{strategyLabel(item.strategy_source)} · {displayTime(item.evaluated_at)}</span><p>{item.reason}</p><small>{parseJsonList(item.evidence_json).slice(0, 3).join('；') || '证据闸门跳过，未生成虚拟委托。'}</small></article>)}
          {!skippedReasons.length && <p className="plain-text">暂无跳过记录。没有记录不代表必须交易，等待系统出现可验证信号。</p>}
        </div>
      </section>
      <section className="simulation-section panel">
        <div className="simulation-section-title"><h4><WalletCards size={17} />当前虚拟持仓</h4><span>{positions.length}只</span></div>
        <div className="simulation-table-wrap"><table className="simulation-table"><thead><tr><th>标的</th><th>数量/可用</th><th>成本/现价</th><th>市值</th><th>浮盈亏</th><th>更新时间</th></tr></thead><tbody>{positions.map(item => <tr key={item.id}><td><b>{item.name}</b><small>{item.code}</small></td><td>{item.quantity.toLocaleString()}<small>可用 {item.available_quantity.toLocaleString()}</small></td><td>{numberValue(item.average_cost)}<small>现 {numberValue(item.market_price)}</small></td><td>{money(item.market_value)}</td><td className={item.unrealized_pnl >= 0 ? 'num-up' : 'num-down'}>{money(item.unrealized_pnl)}</td><td>{displayTime(item.updated_at)}</td></tr>)}{!positions.length && <tr><td colSpan={6}>当前AI模拟账户空仓。空仓也是一个策略动作：说明没有满足证据闸门的机会。</td></tr>}</tbody></table></div>
      </section>
      <section className="ai-trader-review panel">
        <h4>每日复盘与下一步修正</h4>
        <p>{policy}</p>
        <ul>
          <li>买入来源：只允许来自打板预案、预期×量价确认、持仓执行状态机三类可审计信号。</li>
          <li>卖出来源：只允许来自预期证伪、量价转弱、利润保护或硬止损，不因为单一外围/情绪指标机械清仓。</li>
          <li>复盘口径：每日收盘后校准权益，统计胜率、盈亏比、回撤和跳过原因，逐步收紧无效信号。</li>
        </ul>
      </section>
    </div>}
  </section>
}

function AiTraderValidationPanel({ validation }: { validation: SimulationValidation | null }) {
  if (!validation) return null
  const ready = validation.status === 'ready'
  return <section className={`simulation-section panel tone-${ready ? 'ok' : 'pending'}`}>
    <div className="simulation-section-title">
      <div><h4><ShieldAlert size={17} />逐时点样本外验证</h4><small>严格使用决策当时已冻结的数据，探索交易不进入正式成绩。</small></div>
      <span>{ready ? '已有滚动测试窗' : '样本积累中'}</span>
    </div>
    <div className="simulation-kpi-grid">
      <SimulationMetric label="正式逐时点样本" value={`${validation.formal_point_in_time_samples}笔`} detail={`门槛 ${validation.minimum_train_samples}笔训练`} />
      <SimulationMetric label="探索样本" value={`${validation.exploration_samples}笔`} detail="独立分账" />
      <SimulationMetric label="样本外胜率" value={validation.folds.length ? percent(validation.out_of_sample_overall.win_rate) : '--'} detail={`${validation.out_of_sample_overall.sample_count}笔`} />
      <SimulationMetric label="样本外盈亏比" value={validation.folds.length ? numberValue(validation.out_of_sample_overall.profit_loss_ratio) : '--'} />
    </div>
    <p className="plain-text">滚动方式：前{validation.minimum_train_samples}笔只作训练基线，之后每{validation.test_fold_size}笔组成一个完全后置测试窗；当前{validation.folds.length}个测试窗，排除{validation.excluded_samples}笔不符合时点证据契约的样本。</p>
    {!!validation.exclusion_reasons.length && <p className="simulation-sample-warning">排除依据：{validation.exclusion_reasons.join('；')}</p>}
  </section>
}

type AiTraderStrategyGrade = {
  score: number
  label: string
  tone: 'pending' | 'ok' | 'warning' | 'danger'
  reasons: string[]
}

function gradeAiTraderStrategy(performance: SimulationPerformance | null): AiTraderStrategyGrade {
  const formal = performance?.formal
  const samples = formal?.closed_trade_count ?? 0
  if (!performance || samples === 0) {
    return {
      score: 50,
      label: '前向观察期',
      tone: 'pending',
      reasons: ['尚无完整买入—卖出闭环，当前评分只代表规则完整度，不代表收益能力。'],
    }
  }
  const winContribution = Math.min(24, Math.max(0, formal?.win_rate ?? 0) * 0.32)
  const ratioContribution = Math.min(18, Math.max(0, (formal?.profit_loss_ratio ?? 0) - 0.7) * 12)
  const drawdownPenalty = Math.min(30, Math.abs(performance.maximum_drawdown_pct) * 1.5)
  const sampleContribution = Math.min(8, samples * 0.4)
  let score = Math.round(42 + winContribution + ratioContribution + sampleContribution - drawdownPenalty)
  score = Math.max(0, Math.min(samples < 20 ? 68 : 100, score))
  const label = samples < 20 ? '样本积累期' : score >= 75 ? '可继续前向验证' : score >= 60 ? '中性优化' : '需要收紧'
  const tone = score >= 75 ? 'ok' : score >= 55 ? 'warning' : 'danger'
  return {
    score,
    label,
    tone,
    reasons: [
      `正式闭环 ${samples} 笔，胜率 ${percent(formal?.win_rate)}，盈亏比 ${numberValue(formal?.profit_loss_ratio)}；探索样本 ${performance.exploration.closed_trade_count} 笔不参与评分。`,
      `历史最大回撤 ${percent(Math.abs(performance.maximum_drawdown_pct))}；评分会惩罚大回撤，并对不足20笔的样本封顶。`,
    ],
  }
}

function cleanShadowReason(order: SimulationOrder) {
  return order.reject_reason || order.client_note.replace(/^shadow:[^;]+;rule=[^;]+;reason=/, '') || '等待成交或拒绝结果。'
}

function AiTraderFeedbackPanel({
  decisions,
  orders,
  performance,
  calibration,
}: {
  decisions: SimulationShadowDecision[]
  orders: SimulationOrder[]
  performance: SimulationPerformance | null
  calibration: SimulationCalibrationProposal | null
}) {
  const grade = gradeAiTraderStrategy(performance)
  const selected = decisions.filter(item => item.status.toUpperCase() === 'ORDER_CREATED').slice(0, 4)
  const buyOrders = orders.filter(item => item.side === 'BUY').slice(0, 3)
  const sellOrders = orders.filter(item => item.side === 'SELL').slice(0, 3)
  const leadingSlice = [...(performance?.by_strategy ?? [])].sort((left, right) => right.total_realized_pnl - left.total_realized_pnl)[0]
  const optimization = calibration?.candidates.slice(0, 3) ?? []
  return <section className={`ai-trader-feedback panel tone-${grade.tone}`}>
    <header>
      <div>
        <h4><BarChart3 size={18} />策略日记与反馈闭环</h4>
        <p>把当时可见证据、虚拟决策、成交结果和后续校准写在同一页，避免只看盈亏倒推理由。</p>
      </div>
      <div className="ai-trader-grade"><span>当前策略评分</span><strong>{grade.score}</strong><small>{grade.label}</small></div>
    </header>
    <div className="ai-trader-feedback-grid">
      <article>
        <h5>当前交易策略</h5>
        <strong>证据闸门＋小仓位前向验证</strong>
        <p>先用市场环境、主线题材、资金方向和预期×量价筛选，再由打板预案、预期量价或持仓状态机产生可审计信号；证据不足时允许空仓。</p>
        {leadingSlice && <small>当前表现相对最好：{strategyLabel(leadingSlice.key)} · 已实现 {money(leadingSlice.total_realized_pnl)}</small>}
        {grade.reasons.map(reason => <small key={reason}>{reason}</small>)}
      </article>
      <article>
        <h5>今日选股与放行理由</h5>
        {selected.length ? selected.map(item => <div className="ai-trader-journal-row" key={item.id}>
          <b>{item.name || item.code}</b><span>{strategyLabel(item.strategy_source)}</span><p>{item.reason}</p>
          <small>{parseJsonList(item.evidence_json).slice(0, 3).join('；') || '已保存决策证据快照。'}</small>
        </div>) : <p>今日没有标的通过证据闸门。系统仍会记录候选被跳过的原因，并把空仓视为一次纪律决策。</p>}
      </article>
      <article>
        <h5>买入与卖出理由</h5>
        {buyOrders.map(order => <div className="ai-trader-journal-row" key={`buy-${order.id}`}><b>{order.name || order.code} · 虚拟买入</b><p>{cleanShadowReason(order)}</p><small>{statusLabel(order.status)} · {displayTime(order.submitted_at)}</small></div>)}
        {sellOrders.map(order => <div className="ai-trader-journal-row" key={`sell-${order.id}`}><b>{order.name || order.code} · 虚拟卖出</b><p>{cleanShadowReason(order)}</p><small>{statusLabel(order.status)} · {displayTime(order.submitted_at)}</small></div>)}
        {!buyOrders.length && !sellOrders.length && <p>今日尚无虚拟买卖。出现委托后，这里会分别记录买入理由、卖出理由、执行状态和时间。</p>}
      </article>
      <article>
        <h5>复盘与策略修正原因</h5>
        {optimization.length ? optimization.map(item => <div className="ai-trader-journal-row" key={`${item.target}-${item.field}`}>
          <b>{item.direction === 'tighten' ? '建议收紧' : item.direction === 'loosen' ? '建议放宽' : '维持规则'} · {item.target}</b>
          <p>{item.suggestion}</p><small>{item.reason} · {item.support_metric} · 样本 {item.sample_count}</small>
        </div>) : <p>{calibration?.summary || '当前样本尚不足以形成调参候选，继续保留原规则并积累前向样本。'}</p>}
        <small>所有修正先形成候选并保留原因；不会因为少量偶然盈亏自动改写真实交易规则。</small>
      </article>
    </div>
  </section>
}

function automationStepTone(active: boolean, error?: string, time?: string | null): 'running' | 'error' | 'ok' | 'idle' {
  if (active) return 'running'
  if (error) return 'error'
  if (time) return 'ok'
  return 'idle'
}

function automationStatusLabel(active: boolean, error?: string, time?: string | null): string {
  const tone = automationStepTone(active, error, time)
  if (tone === 'running') return '运行中'
  if (tone === 'error') return '异常'
  if (tone === 'ok') return '最近成功'
  return '等待首次运行'
}

function AiTraderAutomationPanel({ status }: { status: IntradayCollectorStatus | null }) {
  const enabled = status?.enabled ?? false
  const cadence = status?.interval_seconds ? `${status.interval_seconds}秒` : '约60秒'
  const steps = [
    {
      title: '盘中行情与证据采集',
      desc: `交易时段自动采集持仓、候选股、量价证据，默认节奏 ${cadence}。`,
      active: Boolean(status?.running),
      time: status?.last_success_at,
      error: status?.last_error,
    },
    {
      title: '市场环境与资金雷达',
      desc: `全市场赚钱效应、量能、板块资金和外围证据低频更新。`,
      active: Boolean(status?.market_regime_running),
      time: status?.market_regime_last_success_at,
      error: status?.market_regime_last_error,
    },
    {
      title: '资讯/机会雷达',
      desc: '盘中捕捉行业要闻、板块突发和机会事件，供虚拟策略引用。',
      active: Boolean(status?.opportunity_radar_running),
      time: status?.opportunity_radar_last_success_at,
      error: status?.opportunity_radar_last_error,
    },
    {
      title: '虚拟委托撮合',
      desc: '先撮合上一轮虚拟委托，再允许新信号进入，避免同一根K线自买自卖。',
      active: Boolean(status?.simulation_match_running),
      time: status?.simulation_match_last_success_at,
      error: status?.simulation_match_last_error,
    },
    {
      title: 'AI虚拟决策扫描',
      desc: '按证据闸门自动选股、分仓、买卖或选择空仓等待，并记录跳过原因。',
      active: Boolean(status?.simulation_shadow_running),
      time: status?.simulation_shadow_last_success_at,
      error: status?.simulation_shadow_last_error,
    },
    {
      title: '收盘复盘与次日剧本',
      desc: `收盘后校准虚拟权益、轮换自动观察池并生成次日预期。${status?.close_expectation_completed_date ? `最近剧本日 ${status.close_expectation_completed_date}。` : ''}`,
      active: false,
      time: status?.simulation_shadow_equity_last_success_at,
      error: status?.simulation_shadow_equity_last_error,
    },
  ]

  return <section className="ai-trader-automation panel">
    <header>
      <div>
        <h4>无人值守运行链路</h4>
        <p>你不用手动点按钮。只要后端服务在运行，系统会按交易时段自动完成“取数 → 判断 → 虚拟下单 → 撮合 → 收盘复盘”。</p>
      </div>
      <span className={`ai-trader-automation-badge ${enabled ? 'is-on' : 'is-off'}`}>{enabled ? '定时任务已启用' : '定时任务未启用'}</span>
    </header>
    <div className="ai-trader-automation-steps">
      {steps.map(step => {
        const tone = automationStepTone(step.active, step.error, step.time)
        return <article key={step.title} className={`tone-${tone}`}>
          <div>
            <strong>{step.title}</strong>
            <span>{automationStatusLabel(step.active, step.error, step.time)}</span>
          </div>
          <p>{step.desc}</p>
          <small>{step.error ? `错误：${step.error}` : `最近成功：${displayTime(step.time || undefined)}`}</small>
        </article>
      })}
    </div>
    <p className="ai-trader-automation-note">手动“扫描一次/校准权益”只用于排查数据源或临时补跑，不是正常交易流程的一部分。</p>
  </section>
}

export function SimulationAccountOverview() {
  const { accounts, activeId, selectAccount, loadAccounts, loadingAccounts, accountError } = useSimulationAccounts()
  const [account, setAccount] = useState<SimulationAccount | null>(null)
  const [positions, setPositions] = useState<SimulationPosition[]>([])
  const [equities, setEquities] = useState<SimulationDailyEquity[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [name, setName] = useState('策略模拟账户')
  const [initialCash, setInitialCash] = useState('1000000')
  const [message, setMessage] = useState('')

  const load = useCallback(() => {
    if (!activeId) { setAccount(null); setPositions([]); setEquities([]); return }
    setLoading(true); setError('')
    Promise.all([
      simulationRequest<SimulationAccount>(`/api/simulation/accounts/${activeId}`),
      simulationRequest<SimulationPosition[]>(`/api/simulation/accounts/${activeId}/positions`),
      simulationRequest<SimulationDailyEquity[]>(`/api/simulation/accounts/${activeId}/equity?limit=500`),
    ]).then(([accountRow, positionRows, equityRows]) => { setAccount(accountRow); setPositions(positionRows); setEquities(equityRows) })
      .catch(value => setError(value instanceof Error ? value.message : '模拟账户概览读取失败')).finally(() => setLoading(false))
  }, [activeId])
  useEffect(() => load(), [load])

  const create = (event: FormEvent) => {
    event.preventDefault(); setMessage('')
    if (!(Number(initialCash) > 0)) return setMessage('初始模拟资金必须大于0。')
    simulationRequest<SimulationAccount>('/api/simulation/accounts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() || '模拟账户', initial_cash: Number(initialCash) }) })
      .then(row => { localStorage.setItem(ACTIVE_ACCOUNT_KEY, String(row.id)); setMessage(`模拟账户 #${row.id} 已创建。`); loadAccounts(); window.dispatchEvent(new Event('simulation-account-changed')) })
      .catch(value => setMessage(value instanceof Error ? value.message : '模拟账户创建失败'))
  }
  const mark = () => {
    if (!activeId) return
    setMessage('正在用当前行情校准模拟权益……')
    simulationRequest<SimulationDailyEquity>(`/api/simulation/accounts/${activeId}/equity/mark`, { method: 'POST' })
      .then(row => { setMessage(`模拟权益已校准，行情时点 ${displayTime(row.captured_at)}。`); load() })
      .catch(value => setMessage(value instanceof Error ? value.message : '模拟权益校准失败'))
  }
  const latest = equities[0]
  const marketValue = latest?.market_value ?? positions.reduce((sum, item) => sum + item.market_value, 0)
  const totalEquity = latest?.total_equity ?? (account ? account.cash + marketValue : null)
  return <section className="simulation-page"><SimulationNotice dataAsOf={latest?.captured_at || account?.updated_at} /><ModuleHeading title="账户概览" subtitle="金额全部来自独立模拟账本，不与真实持仓、真实交易记录混用。" loading={loading || loadingAccounts} onRefresh={() => { loadAccounts(); load() }} extra={<button className="refresh-btn inline" type="button" onClick={mark} disabled={!activeId}><BarChart3 size={15} />市值校准</button>} /><AccountPicker accounts={accounts} activeId={activeId} onSelect={selectAccount} />
    {!accounts.length && !loadingAccounts && <form className="simulation-create-account panel" onSubmit={create}><div className="simulation-form-title"><strong>创建独立模拟账户</strong><span>不读取真实资金</span></div><label>账户名称<input value={name} maxLength={64} onChange={event => setName(event.target.value)} /></label><label>初始模拟资金<input type="number" min="1" step="1" value={initialCash} onChange={event => setInitialCash(event.target.value)} /></label><button className="simulation-submit" type="submit">创建模拟账户</button></form>}
    {message && <p className="simulation-form-message">{message}</p>}
    {!account ? <ModuleState loading={loading || loadingAccounts} error={error || accountError} onRefresh={loadAccounts} empty="尚未创建模拟账户。" /> : <><div className="simulation-account-meta"><div><span>账户</span><strong>{account.name}</strong></div><div><span>状态</span><strong>{statusLabel(account.status)}</strong></div><div><span>佣金 / 最低佣金</span><strong>{percent(account.commission_rate * 100)} / {money(account.minimum_commission)}</strong></div><div><span>印花税 / 过户费</span><strong>{percent(account.stamp_tax_rate * 100)} / {percent(account.transfer_fee_rate * 100)}</strong></div></div><div className="simulation-kpi-grid sensitive-card"><SimulationMetric label="模拟总资产" value={money(totalEquity)} /><SimulationMetric label="可用模拟资金" value={money(account.cash)} /><SimulationMetric label="模拟持仓市值" value={money(marketValue)} /><SimulationMetric label="累计模拟盈亏" value={money(latest?.total_pnl ?? (totalEquity == null ? null : totalEquity - account.initial_cash))} tone={(latest?.total_pnl ?? 0) >= 0 ? 'up' : 'down'} detail={latest ? percent(latest.return_pct, true) : '尚未校准日权益'} /><SimulationMetric label="今日模拟盈亏" value={money(latest?.daily_pnl)} tone={(latest?.daily_pnl ?? 0) >= 0 ? 'up' : 'down'} /><SimulationMetric label="最大当前回撤" value={latest ? percent(Math.abs(latest.drawdown_pct)) : '--'} tone="down" /><SimulationMetric label="初始模拟资金" value={money(account.initial_cash)} /><SimulationMetric label="持仓标的" value={`${positions.length}只`} /></div></>}
  </section>
}

function validateOrder(draft: OrderDraft, confirmed: boolean) {
  const quantity = Number(draft.quantity)
  const price = draft.order_type === 'LIMIT' ? Number(draft.price) : 0
  if (!/^\d{6}$/.test(draft.code.trim())) return '请输入6位股票/ETF代码。'
  if (!Number.isInteger(quantity) || quantity <= 0) return '模拟委托数量必须为正整数。'
  if (draft.side === 'BUY' && quantity % 100 !== 0) return 'A股模拟买入数量必须为100股的整数倍。'
  if (draft.order_type === 'LIMIT' && (!(price > 0))) return '限价模拟委托必须填写有效价格。'
  if (!draft.note.trim()) return '请填写决策依据，方便回放和绩效归因。'
  if (!confirmed) return '请先确认这是模拟委托，不会提交到真实券商。'
  return ''
}
function orderPayload(draft: OrderDraft) {
  return { strategy_source: draft.strategy_source, code: draft.code.trim(), name: '', side: draft.side, order_type: draft.order_type, limit_price: draft.order_type === 'LIMIT' ? Number(draft.price) : 0, quantity: Number(draft.quantity), client_note: draft.note.trim() }
}

export function SimulationOrdersAndPositions() {
  const { accounts, activeId, selectAccount, loadingAccounts, accountError } = useSimulationAccounts()
  const [positions, setPositions] = useState<SimulationPosition[]>([])
  const [orders, setOrders] = useState<SimulationOrder[]>([])
  const [draft, setDraft] = useState<OrderDraft>(emptyOrder)
  const [confirmed, setConfirmed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const load = useCallback(() => {
    if (!activeId) { setPositions([]); setOrders([]); return }
    setLoading(true); setError('')
    Promise.all([simulationRequest<SimulationPosition[]>(`/api/simulation/accounts/${activeId}/positions`), simulationRequest<SimulationOrder[]>(`/api/simulation/accounts/${activeId}/orders?limit=200`)]).then(([positionRows, orderRows]) => { setPositions(positionRows); setOrders(orderRows) }).catch(value => setError(value instanceof Error ? value.message : '模拟委托与持仓读取失败')).finally(() => setLoading(false))
  }, [activeId])
  useEffect(() => load(), [load])
  const submit = (event: FormEvent) => {
    event.preventDefault(); setMessage('')
    const issue = validateOrder(draft, confirmed); if (issue) return setMessage(issue)
    if (!activeId) return setMessage('请先创建或选择模拟账户。')
    setSubmitting(true)
    simulationRequest<SimulationOrder>(`/api/simulation/accounts/${activeId}/orders`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(orderPayload(draft)) }).then(result => { setMessage(`模拟委托 #${result.id} 状态：${statusLabel(result.status)}。${result.reject_reason || ''}`); setDraft(emptyOrder); setConfirmed(false); load() }).catch(value => setMessage(value instanceof Error ? value.message : '模拟委托提交失败')).finally(() => setSubmitting(false))
  }
  const processOrders = () => { if (!activeId) return; setMessage('正在用当前真实行情重新评估未成交模拟委托……'); simulationRequest<SimulationOrder[]>(`/api/simulation/accounts/${activeId}/orders/process`, { method: 'POST' }).then(rows => { setMessage(`已评估 ${rows.length} 笔开放模拟委托。`); load() }).catch(value => setMessage(value instanceof Error ? value.message : '模拟撮合刷新失败')) }
  const cancel = (order: SimulationOrder) => { if (!activeId || !window.confirm(`只撤销 ${order.name || order.code} 的模拟委托，不影响任何真实交易。是否继续？`)) return; simulationRequest<SimulationOrder>(`/api/simulation/accounts/${activeId}/orders/${order.id}/cancel`, { method: 'POST' }).then(() => load()).catch(value => setMessage(value instanceof Error ? value.message : '模拟撤单失败')) }
  const dataAsOf = orders[0]?.last_evaluated_at || positions[0]?.updated_at
  return <section className="simulation-page"><SimulationNotice dataAsOf={dataAsOf} /><ModuleHeading title="模拟委托与持仓" subtitle="模拟委托仅进入独立撮合器；触及涨跌停、非交易时段、行情不实时等情况会保守拒绝并说明原因。" loading={loading || loadingAccounts} onRefresh={load} extra={<button className="refresh-btn inline" type="button" onClick={processOrders} disabled={!activeId}><RefreshCcw size={15} />重新模拟撮合</button>} /><AccountPicker accounts={accounts} activeId={activeId} onSelect={selectAccount} />
    <form className="simulation-order-form panel" onSubmit={submit}><div className="simulation-form-title"><strong>新建模拟委托</strong><span>提交时由后端自动冻结证据快照</span></div><OrderFields draft={draft} setDraft={setDraft} /><label className="simulation-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认这是模拟委托，不会产生真实成交或真实资金变化。</label><button className="simulation-submit" type="submit" disabled={submitting || !confirmed || !activeId}><Send size={15} />{submitting ? '模拟撮合中' : '提交模拟委托'}</button>{message && <p className="simulation-form-message">{message}</p>}</form>
    {!activeId || (error && !positions.length && !orders.length) ? <ModuleState loading={loading || loadingAccounts} error={error || accountError} onRefresh={load} empty="请先在账户概览创建模拟账户。" /> : <><section className="simulation-section panel"><div className="simulation-section-title"><h4><WalletCards size={17} />模拟持仓</h4><span>{positions.length}只</span></div><div className="simulation-table-wrap"><table className="simulation-table"><thead><tr><th>代码/名称</th><th>数量/可用</th><th>成本/现价</th><th>模拟市值</th><th>浮动盈亏</th><th>累计已实现</th><th>行情时点</th></tr></thead><tbody>{positions.map(item => <tr key={item.id}><td><b>{item.name}</b><small>{item.code}</small></td><td>{item.quantity.toLocaleString()}<small>可用 {item.available_quantity.toLocaleString()} · 今日买 {item.today_buy_quantity}</small></td><td>{numberValue(item.average_cost)}<small>现 {numberValue(item.market_price)}</small></td><td className="private-value">{money(item.market_value)}</td><td className={`private-value ${item.unrealized_pnl >= 0 ? 'num-up' : 'num-down'}`}>{money(item.unrealized_pnl)}</td><td className={`private-value ${item.realized_pnl >= 0 ? 'num-up' : 'num-down'}`}>{money(item.realized_pnl)}</td><td>{displayTime(item.updated_at)}</td></tr>)}{!positions.length && <tr><td colSpan={7}>暂无模拟持仓；系统不会用真实持仓填充。</td></tr>}</tbody></table></div></section><section className="simulation-section panel"><div className="simulation-section-title"><h4><History size={17} />模拟委托</h4><span>{orders.length}笔</span></div><div className="simulation-order-list">{orders.map(order => <article key={order.id} className={`simulation-order-row tone-${statusTone(order.status)}`}><div><b>{order.name || order.code}</b><small>{order.code} · #{order.id}</small></div><strong>{order.side === 'BUY' ? '模拟买入' : '模拟卖出'} {order.quantity}股</strong><span>{order.order_type === 'LIMIT' ? `限价 ${numberValue(order.limit_price)}` : '模拟市价撮合'}<small>成交 {order.filled_quantity}股 @ {numberValue(order.average_fill_price)}</small></span><span className={`simulation-status tone-${statusTone(order.status)}`}>{statusLabel(order.status)}</span><span>{strategyLabel(order.strategy_source)}<small>{displayTime(order.submitted_at)}</small></span><span className="simulation-unfilled">{order.reject_reason || (order.status === 'FILLED' ? '全部模拟成交' : '未成交：等待价格触发或下一次模拟撮合')}</span>{['OPEN', 'PENDING', 'PARTIAL'].includes(order.status.toUpperCase()) && <button type="button" onClick={() => cancel(order)}>撤销模拟委托</button>}</article>)}{!orders.length && <p className="plain-text">暂无模拟委托。</p>}</div></section></>}
  </section>
}

function OrderFields({ draft, setDraft }: { draft: OrderDraft; setDraft: Dispatch<SetStateAction<OrderDraft>> }) {
  return <><label>证券代码<input value={draft.code} maxLength={6} inputMode="numeric" onChange={event => setDraft(current => ({ ...current, code: event.target.value.replace(/\D/g, '') }))} placeholder="例如 600584" /></label><label>方向<select value={draft.side} onChange={event => setDraft(current => ({ ...current, side: event.target.value as SimulationOrderSide }))}><option value="BUY">模拟买入</option><option value="SELL">模拟卖出</option></select></label><label>委托类型<select value={draft.order_type} onChange={event => setDraft(current => ({ ...current, order_type: event.target.value as SimulationOrderType }))}><option value="LIMIT">模拟限价</option><option value="MARKET">模拟市价撮合</option></select></label><label>模拟限价<input type="number" min="0" step="0.01" disabled={draft.order_type === 'MARKET'} value={draft.price} onChange={event => setDraft(current => ({ ...current, price: event.target.value }))} placeholder={draft.order_type === 'MARKET' ? '由撮合器计算' : '限价'} /></label><label>数量<input type="number" min="1" step="1" value={draft.quantity} onChange={event => setDraft(current => ({ ...current, quantity: event.target.value }))} /></label><label>策略<select value={draft.strategy_source} onChange={event => setDraft(current => ({ ...current, strategy_source: event.target.value as SimulationStrategyType }))}>{Object.entries(STRATEGY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="simulation-order-reason">决策依据<textarea value={draft.note} maxLength={1000} onChange={event => setDraft(current => ({ ...current, note: event.target.value }))} placeholder="记录预期差、量价、市场环境与失效条件，不要只写‘看涨’。" /></label></>
}

export function SimulationStrategyLab() {
  const { accounts, activeId, selectAccount, loadingAccounts, accountError } = useSimulationAccounts()
  const [selected, setSelected] = useState<SimulationStrategyType>('limit_up')
  const [orders, setOrders] = useState<SimulationOrder[]>([])
  const [performance, setPerformance] = useState<SimulationPerformance | null>(null)
  const [draft, setDraft] = useState<OrderDraft>({ ...emptyOrder, strategy_source: 'limit_up' })
  const [confirmed, setConfirmed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const load = useCallback(() => {
    if (!activeId) { setOrders([]); setPerformance(null); return }
    setLoading(true); setError('')
    Promise.all([simulationRequest<SimulationOrder[]>(`/api/simulation/accounts/${activeId}/orders?limit=200`), simulationRequest<SimulationPerformance>(`/api/simulation/accounts/${activeId}/performance`)]).then(([rows, report]) => { setOrders(rows); setPerformance(report) }).catch(value => setError(value instanceof Error ? value.message : '策略实验账本读取失败')).finally(() => setLoading(false))
  }, [activeId])
  useEffect(() => load(), [load])
  useEffect(() => setDraft(current => ({ ...current, strategy_source: selected })), [selected])
  const submit = (event: FormEvent) => { event.preventDefault(); setMessage(''); const issue = validateOrder(draft, confirmed); if (issue) return setMessage(issue); if (!activeId) return setMessage('请先创建或选择模拟账户。'); simulationRequest<SimulationOrder>(`/api/simulation/accounts/${activeId}/orders`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(orderPayload(draft)) }).then(row => { setMessage(`${STRATEGY_LABELS[selected]}实验委托 #${row.id}：${statusLabel(row.status)}。${row.reject_reason || ''}`); setConfirmed(false); load() }).catch(value => setMessage(value instanceof Error ? value.message : '策略实验委托提交失败')) }
  const details: Array<{ key: SimulationStrategyType; desc: string; gates: string[] }> = [
    { key: 'limit_up', desc: '验证封板质量、题材梯队与次日接力。', gates: ['涨停/炸板数据缺失时不开放', '触及涨停且无卖盘保守按不可成交'] },
    { key: 'expectation_volume_price', desc: '验证预期差、VWAP与反转/证伪。', gates: ['预期与量价证据必须早于委托', '分钟数据缺失时证据质量标记缺失'] },
    { key: 'holding_execution', desc: '验证冲高减仓、恐慌保护和止损。', gates: ['T+1可卖数量硬约束', '卖出与逆势加仓分别验证'] },
  ]
  const strategySlice = performance?.by_strategy.find(item => item.key === selected)
  const strategyOrders = useMemo(() => orders.filter(item => item.strategy_source === selected), [orders, selected])
  return <section className="simulation-page"><SimulationNotice dataAsOf={strategyOrders[0]?.last_evaluated_at} /><ModuleHeading title="策略实验" subtitle="每一笔模拟委托都在提交时冻结行情、市场环境、预期差与量价证据，并按策略来源独立统计。" loading={loading || loadingAccounts} onRefresh={load} /><AccountPicker accounts={accounts} activeId={activeId} onSelect={selectAccount} /><div className="simulation-strategy-cards">{details.map(item => <button type="button" key={item.key} className={selected === item.key ? 'active' : ''} onClick={() => setSelected(item.key)}><FlaskConical size={18} /><strong>{STRATEGY_LABELS[item.key]}</strong><span>{item.desc}</span>{item.gates.map(gate => <small key={gate}>· {gate}</small>)}</button>)}</div>
    <div className="simulation-kpi-grid"><SimulationMetric label="已完成闭环交易" value={`${strategySlice?.closed_trade_count ?? 0}笔`} /><SimulationMetric label="策略胜率" value={strategySlice ? percent(strategySlice.win_rate) : '--'} /><SimulationMetric label="策略盈亏比" value={strategySlice ? numberValue(strategySlice.profit_loss_ratio) : '--'} /><SimulationMetric label="策略已实现盈亏" value={strategySlice ? money(strategySlice.total_realized_pnl) : '--'} tone={(strategySlice?.total_realized_pnl ?? 0) >= 0 ? 'up' : 'down'} /></div>
    <form className="simulation-order-form panel" onSubmit={submit}><div className="simulation-form-title"><strong>{STRATEGY_LABELS[selected]}实时模拟实验</strong><span>只生成模拟委托，不做历史数据回填成交</span></div><OrderFields draft={draft} setDraft={setDraft} /><label className="simulation-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认本次仅写入模拟账本。</label><button className="simulation-submit" type="submit" disabled={!confirmed || !activeId}><Send size={15} />提交策略实验委托</button>{message && <p className="simulation-form-message">{message}</p>}</form>
    {error || accountError ? <ModuleState loading={loading || loadingAccounts} error={error || accountError} onRefresh={load} /> : <section className="simulation-section panel"><div className="simulation-section-title"><h4>本策略最近实验</h4><span>{strategyOrders.length}笔</span></div><div className="simulation-experiment-list">{strategyOrders.slice(0, 20).map(item => <article key={item.id}><div><b>{item.name || item.code}</b><span className={`simulation-status tone-${statusTone(item.status)}`}>{statusLabel(item.status)}</span></div><strong>{item.side === 'BUY' ? '模拟买入' : '模拟卖出'} {item.quantity}股 · {item.order_type === 'LIMIT' ? numberValue(item.limit_price) : '市价撮合'}</strong><p>{item.client_note || '未记录实验假设。'}</p><small>决策证据 #{item.decision_evidence_snapshot_id} · {displayTime(item.last_evaluated_at)}</small>{item.reject_reason && <div className="simulation-missing"><AlertTriangle size={14} />{item.reject_reason}</div>}</article>)}</div></section>}
  </section>
}

function parseJsonRecord(raw: string) {
  try { const value = JSON.parse(raw) as unknown; return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {} } catch { return {} }
}
export function SimulationEvidenceLedger() {
  const { accounts, activeId, selectAccount, loadingAccounts, accountError } = useSimulationAccounts()
  const [orders, setOrders] = useState<SimulationOrder[]>([])
  const [fills, setFills] = useState<SimulationFill[]>([])
  const [evidence, setEvidence] = useState<SimulationEvidence[]>([])
  const [shadowDecisions, setShadowDecisions] = useState<SimulationShadowDecision[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback(() => {
    if (!activeId) { setOrders([]); setFills([]); setEvidence([]); setShadowDecisions([]); return }
    setLoading(true); setError('')
    Promise.all([simulationRequest<SimulationOrder[]>(`/api/simulation/accounts/${activeId}/orders?limit=200`), simulationRequest<SimulationFill[]>(`/api/simulation/accounts/${activeId}/fills?limit=500`), simulationRequest<SimulationEvidence[]>(`/api/simulation/accounts/${activeId}/evidence?limit=200`), simulationRequest<SimulationShadowDecision[]>(`/api/simulation/accounts/${activeId}/shadow-decisions?limit=200`)]).then(([orderRows, fillRows, evidenceRows, shadowRows]) => { setOrders(orderRows); setFills(fillRows); setEvidence(evidenceRows); setShadowDecisions(shadowRows) }).catch(value => setError(value instanceof Error ? value.message : '模拟成交证据读取失败')).finally(() => setLoading(false))
  }, [activeId])
  useEffect(() => load(), [load])
  const fillsByOrder = useMemo(() => new Map(fills.map(item => [item.order_id, item])), [fills])
  const evidenceById = useMemo(() => new Map(evidence.map(item => [item.id, item])), [evidence])
  const selectedAccount = accounts.find(item => item.id === activeId)
  return <section className="simulation-page"><SimulationNotice dataAsOf={evidence[0]?.captured_at} /><ModuleHeading title="成交与决策证据" subtitle="每次模拟成交或拒绝都可追溯到委托前冻结的行情、市场、预期和量价快照。" loading={loading || loadingAccounts} onRefresh={load} /><AccountPicker accounts={accounts} activeId={activeId} onSelect={selectAccount} />
    {error || accountError ? <ModuleState loading={loading || loadingAccounts} error={error || accountError} onRefresh={load} /> : <><ShadowDecisionAudit rows={shadowDecisions} visible={selectedAccount?.account_type === 'shadow' || shadowDecisions.length > 0} /><div className="simulation-evidence-list">{orders.map(order => { const fill = fillsByOrder.get(order.id); const decisionSnapshot = evidenceById.get(order.decision_evidence_snapshot_id); const fillSnapshot = fill ? evidenceById.get(fill.fill_evidence_snapshot_id) : undefined; const sourceVersions = decisionSnapshot ? parseJsonRecord(decisionSnapshot.source_versions_json) : {}; return <article className="panel" key={order.id}><header><div><b>{order.name || order.code}</b><span>{order.code} · {order.side === 'BUY' ? '模拟买入' : '模拟卖出'} · 委托 #{order.id}</span></div><strong className={`simulation-status tone-${statusTone(order.status)}`}>{statusLabel(order.status)}</strong></header><div className="simulation-evidence-summary"><span>策略：<b>{strategyLabel(order.strategy_source)}</b></span><span>委托/成交：<b>{order.order_type === 'LIMIT' ? numberValue(order.limit_price) : '模拟市价'} / {fill ? numberValue(fill.price) : '--'}</b></span><span>成交数量：<b>{fill?.quantity ?? 0} / {order.quantity}股</b></span><span>决策/撮合行情：<b>{displayTime(decisionSnapshot?.quote_time)} / {displayTime(fillSnapshot?.quote_time)}</b></span></div>{order.reject_reason && <p className="simulation-unfilled"><XCircle size={15} />未成交：{order.reject_reason}</p>}<div className="simulation-evidence-tags"><span>决策数据质量：{decisionSnapshot?.data_quality || '证据缺失'}</span><span>市场：{REGIME_LABELS[decisionSnapshot?.market_regime || 'UNKNOWN'] || decisionSnapshot?.market_regime}</span><span>预期差：{decisionSnapshot ? `${GAP_LABELS[decisionSnapshot.expectation_gap_band] || decisionSnapshot.expectation_gap_band}（${decisionSnapshot.expectation_gap_score}）` : '--'}</span><span>量价：{decisionSnapshot?.volume_price_state || '--'}</span><span>板块：{decisionSnapshot?.sector_state || '--'}</span></div><details><summary>查看决策与成交冻结证据</summary>{decisionSnapshot ? <div className="simulation-evidence-ref"><b>决策证据 #{decisionSnapshot.id} · V{decisionSnapshot.version}</b><span>内容指纹 {decisionSnapshot.content_hash.slice(0, 16)}…</span><small>{Object.entries(sourceVersions).map(([key, value]) => `${key}=${String(value ?? '--')}`).join('；') || '来源版本缺失'}</small></div> : <p>决策证据快照缺失，不能把本笔模拟结果用于绩效归因。</p>}{fillSnapshot ? <div className="simulation-evidence-ref"><b>成交证据 #{fillSnapshot.id} · V{fillSnapshot.version}</b><span>内容指纹 {fillSnapshot.content_hash.slice(0, 16)}…</span><small>仅用于复核模拟成交，不参与策略绩效归因。</small></div> : <p>尚无成交证据快照。</p>}</details></article> })}{!orders.length && <ModuleState loading={loading || loadingAccounts} error="" onRefresh={load} empty="暂无模拟委托，因此没有成交与决策证据。" />}</div></>}
  </section>
}

function parseJsonList(raw: string) {
  try { const value = JSON.parse(raw) as unknown; return Array.isArray(value) ? value.map(String) : [] } catch { return [] }
}
function ShadowDecisionAudit({ rows, visible }: { rows: SimulationShadowDecision[]; visible: boolean }) {
  if (!visible) return null
  return <section className="simulation-shadow-audit panel"><header><div><h4><History size={17} />自动影子信号审计</h4><p>每分钟只消费当日、新鲜且已确认的预期×量价、打板或持仓信号；本分钟生成，最早下一分钟模拟撮合。</p></div><span>{rows.length}条</span></header>{rows.length ? <div>{rows.slice(0, 30).map(row => <article key={row.id}><div><b>{row.name || row.code}</b><small>{row.code} · {strategyLabel(row.strategy_source)} · {displayTime(row.evaluated_at)}</small></div><strong className={`simulation-status tone-${statusTone(row.status)}`}>{statusLabel(row.status)}</strong><p>{row.reason}</p><small>{parseJsonList(row.evidence_json).slice(0, 3).join('；') || '本条仅记录闸门跳过原因。'} · 规则 {row.rule_version} · 证据版本 {row.source_version || '--'}</small></article>)}</div> : <p className="plain-text">影子账户已启用，等待交易时段出现可验证的明确策略信号。</p>}<footer>这里只写入模拟账本；不会连接券商、不会修改真实持仓，也不会自动把校准候选应用到实盘规则。</footer></section>
}

export function SimulationPerformanceDesk() {
  const { accounts, activeId, selectAccount, loadingAccounts, accountError } = useSimulationAccounts()
  const [data, setData] = useState<SimulationPerformance | null>(null)
  const [calibration, setCalibration] = useState<SimulationCalibrationProposal | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback(() => {
    if (!activeId) { setData(null); setCalibration(null); return }
    setLoading(true); setError('')
    Promise.all([
      simulationRequest<SimulationPerformance>(`/api/simulation/accounts/${activeId}/performance`),
      simulationRequest<SimulationCalibrationProposal>(`/api/simulation/accounts/${activeId}/calibration-proposal`),
    ]).then(([performance, proposal]) => { setData(performance); setCalibration(proposal) }).catch(value => setError(value instanceof Error ? value.message : '模拟绩效读取失败')).finally(() => setLoading(false))
  }, [activeId])
  useEffect(() => load(), [load])
  const accountAsOf = accounts.find(item => item.id === activeId)?.updated_at
  return <section className="simulation-page"><SimulationNotice dataAsOf={accountAsOf} /><ModuleHeading title="绩效统计" subtitle="只统计已完成的模拟卖出，并按策略、市场环境和预期差分层；比例字段均按百分数显示。" loading={loading || loadingAccounts} onRefresh={load} /><AccountPicker accounts={accounts} activeId={activeId} onSelect={selectAccount} />
    {!data ? <ModuleState loading={loading || loadingAccounts} error={error || accountError} onRefresh={load} empty="没有已完成闭环交易，暂不计算胜率、盈亏比与回撤。" /> : <><div className="simulation-kpi-grid"><SimulationMetric label="已实现模拟盈亏" value={money(data.total_realized_pnl)} tone={data.total_realized_pnl >= 0 ? 'up' : 'down'} /><SimulationMetric label="闭环胜率" value={percent(data.win_rate)} detail={`${data.win_count}胜 / ${data.loss_count}负 / ${data.closed_trade_count}笔完整交易`} /><SimulationMetric label="盈亏比" value={numberValue(data.profit_loss_ratio)} /><SimulationMetric label="最大回撤" value={percent(data.maximum_drawdown_pct)} tone="down" /></div>{data.closed_trade_count < 20 && <p className="simulation-sample-warning"><AlertTriangle size={16} />当前仅 {data.closed_trade_count} 笔完整开平仓交易，分批卖出不会重复计数；样本仍不稳定，不能据此放大模拟仓位或外推真实收益。</p>}<CalibrationCandidates proposal={calibration} /><div className="simulation-breakdown-grid"><PerformanceBreakdown title="按入场策略分层" items={data.by_strategy} label={strategyLabel} /><PerformanceBreakdown title="按入场市场环境分层" items={data.by_market_regime} label={key => REGIME_LABELS[key] || key} /><PerformanceBreakdown title="按入场预期差分层" items={data.by_expectation_gap} label={key => GAP_LABELS[key] || key} /></div></>}
  </section>
}

function CalibrationCandidates({ proposal }: { proposal: SimulationCalibrationProposal | null }) {
  if (!proposal) return null
  const ready = proposal.status === 'READY_FOR_REVIEW'
  return <section className={`simulation-calibration panel ${ready ? 'is-ready' : ''}`}>
    <header><div><h4><ShieldAlert size={17} />样本门槛校准候选</h4><p>{proposal.summary}</p></div><span>{proposal.statistics_only ? `${proposal.statistical_sample_count} 个手工统计样本` : `${proposal.usable_sample_count}/${proposal.minimum_samples} 个自动影子样本`}</span></header>
    {proposal.excluded_sample_count > 0 && <p className="simulation-sample-warning">已排除 {proposal.excluded_sample_count} 笔不符合自动校准证据契约的样本。{proposal.exclusion_reasons?.length ? ` ${proposal.exclusion_reasons.join('；')}` : ''}</p>}
    {proposal.candidates.length > 0 ? <div className="simulation-calibration-list">{proposal.candidates.map(item => <article key={`${item.target}-${item.field}`}><div><strong>{item.target}</strong><span>{item.direction === 'tighten' ? '建议收紧' : item.direction === 'loosen' ? '建议放宽' : '保持规则'}</span></div><p>{item.suggestion}</p><small>{item.reason} · {item.support_metric} · 样本 {item.sample_count}</small></article>)}</div> : <p className="plain-text">尚未形成可执行的调参候选；系统会继续前向采样，不会因少量偶然盈亏自行改规则。</p>}
    <footer>候选只进入人工审核和新旧规则并行影子验证；不会自动修改真实规则，也不会触发真实交易。</footer>
  </section>
}

function SimulationMetric({ label, value, detail, tone = 'neutral' }: { label: string; value: string; detail?: string; tone?: 'neutral' | 'up' | 'down' }) { return <article className={`simulation-metric tone-${tone}`}><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</article> }
function PerformanceBreakdown({ title, items, label }: { title: string; items: SimulationPerformanceSlice[]; label: (key: string) => string }) {
  return <section className="simulation-breakdown panel"><h4>{title}</h4>{items.length ? <div className="simulation-table-wrap"><table className="simulation-table"><thead><tr><th>分层</th><th>闭环样本</th><th>胜/负</th><th>胜率</th><th>平均盈利</th><th>平均亏损</th><th>盈亏比</th><th>已实现盈亏</th></tr></thead><tbody>{items.map(item => <tr key={item.key}><td><b>{label(item.key)}</b></td><td>{item.closed_trade_count}</td><td>{item.win_count}/{item.loss_count}</td><td>{percent(item.win_rate)}</td><td className="num-up">{money(item.average_win)}</td><td className="num-down">{money(item.average_loss)}</td><td>{numberValue(item.profit_loss_ratio)}</td><td className={item.total_realized_pnl >= 0 ? 'num-up' : 'num-down'}>{money(item.total_realized_pnl)}</td></tr>)}</tbody></table></div> : <p className="plain-text">本分层暂无已完成开平仓闭环样本。</p>}</section>
}
