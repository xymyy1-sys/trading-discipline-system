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
