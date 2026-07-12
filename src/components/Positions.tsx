import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Pencil, Plus, RefreshCcw, Save, ShieldAlert, Trash2, X } from 'lucide-react'
import { API_BASE } from '../api'
import { chineseEvidence, chineseLabel } from '../labels'

import type {
  HoldingOut as Holding,
  SectorRotationItem as RotationItem,
  MarketSeesaw as SeesawMonitor,
  PositionExecutionState,
  TimeStopRule,
  TTradePlan,
  AccountRisk,
} from '../types'

export default function Positions() {
  const [holdings, setHoldings] = useState<Holding[]>([])
  const [seesaw, setSeesaw] = useState<SeesawMonitor | null>(null)
  const [executionStates, setExecutionStates] = useState<PositionExecutionState[]>([])
  const [timeStopRules, setTimeStopRules] = useState<TimeStopRule[]>([])
  const [feedbackMessage, setFeedbackMessage] = useState('')
  const [tPlanMessage, setTPlanMessage] = useState('')
  const [tPlans, setTPlans] = useState<Record<number, TTradePlan>>({})
  const [ruleMessage, setRuleMessage] = useState('')
  const [accountAsset, setAccountAsset] = useState('')
  const [openingAsset, setOpeningAsset] = useState('')
  const [accountRisk, setAccountRisk] = useState<AccountRisk | null>(null)
  const [assetSaving, setAssetSaving] = useState(false)
  const [assetMessage, setAssetMessage] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [refreshMessage, setRefreshMessage] = useState('')
  const [holdingsError, setHoldingsError] = useState('')
  const refreshingRef = useRef(false)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState({ code: '', name: '', quantity: '', cost_price: '', current_price: '', position_type: '盈利趋势仓', next_discipline: '' })

  const applyHoldings = (data: Holding[], messagePrefix = '已重算') => {
    setHoldings(data)
    const failed = data.some(item => item.price_source !== 'realtime')
    const now = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    setRefreshMessage(failed ? `${now} ${messagePrefix}，部分行情使用缓存/手工价` : `${now} ${messagePrefix}，已按实时行情更新`)
  }

  const fetchSeesaw = (force = false) => {
    fetch(`${API_BASE}/api/market/seesaw-monitor${force ? '?force_refresh=true' : ''}`)
      .then(r => r.json())
      .then((data: SeesawMonitor) => setSeesaw(data))
      .catch(() => {})
  }

  const fetchExecutionStates = (force = false) => {
    fetch(`${API_BASE}/api/holdings/execution-states${force ? '?force_refresh=true' : ''}`)
      .then(r => r.json())
      .then((data: PositionExecutionState[]) => setExecutionStates(data))
      .catch(() => setExecutionStates([]))
  }

  const fetchTimeStopRules = () => {
    fetch(`${API_BASE}/api/time-stop-rules`)
      .then(r => r.json())
      .then((data: TimeStopRule[]) => setTimeStopRules(data))
      .catch(() => setTimeStopRules([]))
  }

  const fetchTPlans = () => {
    fetch(`${API_BASE}/api/t-plans?active_only=true`)
      .then(r => r.json())
      .then((plans: TTradePlan[]) => setTPlans(Object.fromEntries(plans.map(plan => [plan.holding_id, plan]))))
      .catch(() => setTPlans({}))
  }

  const fetchHoldings = () => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    setRefreshing(true)
    setRefreshMessage('')
    setHoldingsError('')
    fetch(`${API_BASE}/api/holdings`)
      .then(async r => {
        if (!r.ok) throw new Error(`持仓接口返回 ${r.status}`)
        return r.json()
      })
      .then((data: Holding[]) => applyHoldings(data))
      .then(() => {
        fetchSeesaw()
        fetchExecutionStates()
      })
      .catch(error => {
        setHoldingsError(error instanceof Error ? error.message : '持仓加载失败')
        setRefreshMessage('持仓加载失败，页面不会把接口故障误报为“暂无持仓”')
      })
      .finally(() => {
        refreshingRef.current = false
        setRefreshing(false)
      })
  }

  const refreshQuotes = () => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    setRefreshing(true)
    setRefreshMessage('')
    fetch(`${API_BASE}/api/holdings/refresh`, { method: 'POST' })
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((data: { holdings: Holding[]; success_count: number; fallback_count: number; notes: string[] }) => {
        applyHoldings(data.holdings, `刷新完成：实时 ${data.success_count} 只，缓存/手工 ${data.fallback_count} 只`)
        fetchSeesaw(true)
        fetchExecutionStates(true)
        if (data.notes.length) setRefreshMessage(prev => `${prev}；${data.notes.slice(0, 2).join('；')}`)
      })
      .catch(() => setRefreshMessage('行情刷新失败，暂用已有价格'))
      .finally(() => {
        refreshingRef.current = false
        setRefreshing(false)
      })
  }

  const syncFromTrades = () => {
    if (!window.confirm('确认按交易记录重算持仓？同代码持仓会以买入/卖出/加仓/减仓流水为准。')) return
    setSyncing(true)
    setRefreshMessage('')
    fetch(`${API_BASE}/api/holdings/sync-from-trades`, { method: 'POST' })
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((data: { holdings: Holding[]; trade_count: number; notes: string[] }) => {
        applyHoldings(data.holdings, `已按 ${data.trade_count} 条交易记录同步`)
        if (data.notes.length) setRefreshMessage(prev => `${prev}；${data.notes[0]}`)
      })
      .catch(() => setRefreshMessage('交易记录同步失败'))
      .finally(() => setSyncing(false))
  }
  const fetchAccountAsset = () => {
    fetch(`${API_BASE}/api/account/asset`)
      .then(r => r.json())
      .then(data => setAccountAsset(data.total_asset ? String(data.total_asset) : ''))
      .catch(() => {})
    fetch(`${API_BASE}/api/account/risk`)
      .then(r => r.json())
      .then((data: AccountRisk) => {
        setAccountRisk(data)
        setOpeningAsset(data.opening_asset ? String(data.opening_asset) : '')
      })
      .catch(() => {})
  }
  useEffect(() => {
    fetchAccountAsset()
    fetchHoldings()
    fetchSeesaw()
    fetchExecutionStates()
    fetchTimeStopRules()
    fetchTPlans()
  }, [])

  const totalMarketValue = holdings.reduce((s, h) => s + h.market_value, 0)
  const totalPnL = holdings.reduce((s, h) => s + h.profit_amount, 0)
  const totalTodayPnL = holdings.reduce((s, h) => s + h.today_profit_amount, 0)
  const savedTotalAsset = Number(accountAsset) || holdings.find(h => h.total_asset > 0)?.total_asset || 0
  const cashAvailable = savedTotalAsset ? savedTotalAsset - totalMarketValue : 0
  const totalPositionRatio = savedTotalAsset ? totalMarketValue / savedTotalAsset : 0
  const highRiskAlerts = (seesaw?.holding_alerts ?? []).filter(item => ['高', '中高', '中'].includes(item.risk_level))
  const executionByCode = new Map(executionStates.map(item => [item.code, item]))
  const urgentExecutions = executionStates.filter(item => ['EXIT_REQUIRED', 'REDUCE_REQUIRED', 'PROFIT_PROTECTION', 'EXPECTATION_INVALIDATED'].includes(item.state))
  const money = (value: number) => `${value >= 0 ? '+' : ''}${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} 元`

  const resetForm = () => {
    setForm({ code: '', name: '', quantity: '', cost_price: '', current_price: '', position_type: '盈利趋势仓', next_discipline: '' })
    setEditingId(null)
    setShowForm(false)
  }

  const saveAccountAsset = () => {
    setAssetSaving(true)
    setAssetMessage('')
    fetch(`${API_BASE}/api/account/asset`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ total_asset: Number(accountAsset) || 0 }),
    })
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then(data => {
        setAccountAsset(data.total_asset ? String(data.total_asset) : '')
        setAssetMessage('已保存')
        fetchHoldings()
      })
      .catch(() => setAssetMessage('保存失败'))
      .finally(() => setAssetSaving(false))
  }

  const saveHolding = () => {
    const url = editingId ? `${API_BASE}/api/holdings/${editingId}` : `${API_BASE}/api/holdings`
    fetch(url, {
      method: editingId ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: form.code,
        name: form.name,
        quantity: Number(form.quantity),
        cost_price: Number(form.cost_price),
        current_price: Number(form.current_price),
        total_asset: Number(accountAsset) || 0,
        position_type: form.position_type,
        next_discipline: form.next_discipline,
      }),
    })
      .then(r => r.json())
      .then(() => { resetForm(); fetchHoldings() })
      .catch(() => {})
  }

  const startEdit = (h: Holding) => {
    setEditingId(h.id)
    setShowForm(true)
    setForm({
      code: h.code,
      name: h.name,
      quantity: String(h.quantity),
      cost_price: String(h.cost_price),
      current_price: String(h.current_price),
      position_type: h.position_type,
      next_discipline: h.next_discipline,
    })
  }

  const deleteHolding = (h: Holding) => {
    if (!window.confirm(`确认删除持仓 ${h.name}？`)) return
    fetch(`${API_BASE}/api/holdings/${h.id}`, { method: 'DELETE' })
      .then(() => fetchHoldings())
      .catch(() => {})
  }

  const typeColor = (t: string) => {
    if (t.includes('盈利')) return 'var(--up)'
    if (t.includes('亏损') || t.includes('退出')) return 'var(--down)'
    return 'var(--warn)'
  }

  const seesawByCode = new Map((seesaw?.holding_alerts ?? []).map(item => [item.code, item]))
  const riskColor = (level: string) => {
    if (level === '高') return 'var(--down)'
    if (level === '中高') return 'var(--warn)'
    if (level === '中') return '#b7791f'
    return 'var(--text-muted)'
  }
  const actionColor = (state: string) => {
    if (['EXIT_REQUIRED', 'EXPECTATION_INVALIDATED'].includes(state)) return 'var(--down)'
    if (state === 'REDUCE_REQUIRED') return 'var(--warn)'
    if (state === 'PROFIT_PROTECTION') return 'var(--info)'
    if (state === 'PROFIT_EXPANSION') return 'var(--up)'
    return 'var(--ink-muted)'
  }
  const timeLabel = (value: string | null | undefined) => {
    if (!value) return '--'
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    return date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
  }
  const sendFeedback = (state: PositionExecutionState, status: string) => {
    const recommendationId = state.recommendation?.id
    if (!recommendationId) return
    setFeedbackMessage('')
    fetch(`${API_BASE}/api/recommendations/${recommendationId}/execution-feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, reason: status === '暂不执行' ? '盘中继续观察，等待反抽确认。' : '' }),
    })
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then(() => setFeedbackMessage(`${state.name} 已记录：${status}`))
      .catch(() => setFeedbackMessage('执行反馈记录失败'))
  }
  const createTPlan = (state: PositionExecutionState) => {
    setTPlanMessage('')
    fetch(`${API_BASE}/api/holdings/${state.holding_id}/t-plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        t_type: 'NO_T',
        planned_sell_price: 0,
        planned_sell_quantity: 0,
        buyback_price_low: 0,
        buyback_price_high: 0,
        buyback_conditions: [],
        cancel_conditions: [],
      }),
    })
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((plan: TTradePlan) => {
        setTPlans(prev => ({ ...prev, [plan.holding_id]: plan }))
        setTPlanMessage(plan.status === 'forbidden'
          ? `${state.name} 当前禁止做T：${plan.evidence[0] || '交易逻辑不成立'}`
          : `${state.name} 已生成${plan.t_type}计划：卖出 ${plan.planned_sell_quantity} 股，接回 ${plan.buyback_price_low.toFixed(2)}-${plan.buyback_price_high.toFixed(2)}`)
      })
      .catch(() => setTPlanMessage('做T计划生成失败'))
  }

  const saveAccountRisk = () => {
    setAssetSaving(true)
    fetch(`${API_BASE}/api/account/risk`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ opening_asset: Number(openingAsset) || 0, current_asset: Number(accountAsset) || 0 }),
    })
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((data: AccountRisk) => {
        setAccountRisk(data)
        setAssetMessage('账户风险基线已保存')
      })
      .catch(() => setAssetMessage('账户风险基线保存失败'))
      .finally(() => setAssetSaving(false))
  }

  const updateTExecution = (plan: TTradePlan, step: 'sell' | 'buyback' | 'reduction') => {
    if (!plan.id) return
    const remaining = Math.max(0, plan.actual_sell_quantity - plan.actual_buyback_quantity)
    const defaultQuantity = step === 'sell' ? plan.planned_sell_quantity : plan.actual_sell_quantity
    const quantityText = step === 'reduction' ? String(remaining) : window.prompt(step === 'sell' ? '实际卖出数量' : '本次累计接回数量', String(defaultQuantity))
    if (quantityText === null) return
    const quantity = Number(quantityText)
    if (!Number.isInteger(quantity) || quantity < 0) {
      setTPlanMessage('数量必须是非负整数')
      return
    }
    const priceText = step === 'reduction' ? null : window.prompt(step === 'sell' ? '实际卖出价格' : '实际接回价格')
    if (step !== 'reduction' && priceText === null) return
    const price = Number(priceText)
    const payload = step === 'sell'
      ? { actual_sell_quantity: quantity, actual_sell_price: price, execution_note: '前端确认卖出成交' }
      : step === 'buyback'
        ? { actual_buyback_quantity: quantity, actual_buyback_price: price, execution_note: '前端确认接回成交' }
        : { status: 'converted_to_reduction', execution_note: `剩余 ${remaining} 股转为永久减仓` }
    fetch(`${API_BASE}/api/holdings/${plan.holding_id}/t-plan/${plan.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(async response => {
        if (!response.ok) throw new Error((await response.json()).detail || '更新失败')
        return response.json()
      })
      .then((saved: TTradePlan) => {
        setTPlans(prev => ({ ...prev, [saved.holding_id]: saved }))
        setTPlanMessage(`${saved.name} T计划已更新：${saved.status}`)
      })
      .catch(error => setTPlanMessage(error instanceof Error ? error.message : 'T执行反馈失败'))
  }
  const updateTimeStopRule = (rule: TimeStopRule, patch: Partial<TimeStopRule>) => {
    const next = { ...rule, ...patch }
    setTimeStopRules(prev => prev.map(item => item.script_type === rule.script_type ? next : item))
  }
  const saveTimeStopRule = (rule: TimeStopRule) => {
    setRuleMessage('')
    fetch(`${API_BASE}/api/time-stop-rules/${rule.script_type}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        confirmation_deadline: rule.confirmation_deadline,
        below_vwap_minutes: rule.below_vwap_minutes,
        below_vwap_min_bars: rule.below_vwap_min_bars,
        recent_window_minutes: rule.recent_window_minutes,
        failed_limit_reseal_pct: rule.failed_limit_reseal_pct,
        enabled: rule.enabled,
      }),
    })
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((saved: TimeStopRule) => {
        setTimeStopRules(prev => prev.map(item => item.script_type === saved.script_type ? saved : item))
        setRuleMessage(`${saved.display_name} 时间止损规则已保存`)
      })
      .catch(() => setRuleMessage('时间止损规则保存失败'))
  }

  return (
    <div className="pos-layout">
      <header className="pos-header">
        <div>
          <h2>持仓快照</h2>
          <p>当前持仓成本、浮盈浮亏、止损价、利润保护线、仓位类型、下一步纪律。</p>
        </div>
        <div className="header-actions">
          <button className="refresh-btn inline" type="button" onClick={fetchHoldings} disabled={refreshing}>
            <RefreshCcw size={16} />
            {refreshing ? '刷新中' : '重新读取'}
          </button>
          <button className="refresh-btn inline" type="button" onClick={refreshQuotes} disabled={refreshing}>
            <RefreshCcw size={16} />
            {refreshing ? '刷新中' : '刷新行情'}
          </button>
          <button className="refresh-btn inline" type="button" onClick={syncFromTrades} disabled={syncing}>
            <RefreshCcw size={16} />
            {syncing ? '同步中' : '同步交易记录'}
          </button>
          <button className="grade-btn" onClick={() => showForm ? resetForm() : setShowForm(true)}>
            {showForm ? <X size={16} /> : <Plus size={16} />}
            {showForm ? '取消' : '添加持仓'}
          </button>
        </div>
      </header>
      {refreshMessage && <p className="refresh-note">{refreshMessage}</p>}
      {feedbackMessage && <p className="refresh-note">{feedbackMessage}</p>}
      {tPlanMessage && <p className="refresh-note">{tPlanMessage}</p>}

      <section className="panel account-asset-panel">
        <div>
          <span className="eyebrow">账户资产</span>
          <h3>账户总资产</h3>
          <p>仓位按单只持仓市值 / 总资产计算。未填写时仓位显示为空，避免误判为满仓。</p>
        </div>
        <div className="asset-editor">
          <input
            type="number"
            placeholder="填写总资产"
            value={accountAsset}
            onChange={e => {
              setAccountAsset(e.target.value)
              setAssetMessage('')
            }}
          />
          <button className="grade-btn" onClick={saveAccountAsset} disabled={assetSaving}>
            <Save size={16} /> {assetSaving ? '保存中' : '保存总资产'}
          </button>
          {assetMessage && <span className="asset-message">{assetMessage}</span>}
        </div>
        <div className="asset-editor">
          <input type="number" placeholder="当日期初资产" value={openingAsset} onChange={e => setOpeningAsset(e.target.value)} />
          <button className="grade-btn" onClick={saveAccountRisk} disabled={assetSaving}>
            <ShieldAlert size={16} /> 保存今日风控基线
          </button>
        </div>
        {accountRisk && <div className="account-risk-summary">
          <strong>账户风险 {chineseLabel(accountRisk.level)}</strong>
          <span className={accountRisk.daily_profit_ratio >= 0 ? 'num-up' : 'num-down'}>{accountRisk.data_complete ? `${accountRisk.daily_profit_ratio.toFixed(2)}%` : '待设置期初资产'}</span>
          <p>{accountRisk.recommended_action}</p>
          {accountRisk.evidence.map(item => <small key={item}>{item}</small>)}
        </div>}
      </section>

      {timeStopRules.length > 0 && (
        <section className="panel time-stop-rules-panel">
          <div className="selected-theme-head">
            <div>
              <strong>时间止损规则</strong>
              <span>按剧本类型控制确认截止、持续低于分时均价和冲板未回封阈值。</span>
            </div>
            {ruleMessage && <span className="asset-message">{ruleMessage}</span>}
          </div>
          <div className="time-stop-rule-grid">
            {timeStopRules.map(rule => (
              <article key={rule.script_type} className="time-stop-rule-card">
                <header>
                  <b>{rule.display_name}</b>
                  <label>
                    <input
                      type="checkbox"
                      checked={rule.enabled}
                      onChange={event => updateTimeStopRule(rule, { enabled: event.target.checked })}
                    />
                    启用
                  </label>
                </header>
                <div className="time-stop-rule-fields">
                  <label>
                    <span>确认截止</span>
                    <input
                      value={rule.confirmation_deadline}
                      onChange={event => updateTimeStopRule(rule, { confirmation_deadline: event.target.value })}
                    />
                  </label>
                  <label>
                    <span>低于分时均价分钟</span>
                    <input
                      type="number"
                      min="1"
                      max="60"
                      value={rule.below_vwap_minutes}
                      onChange={event => updateTimeStopRule(rule, { below_vwap_minutes: Number(event.target.value) })}
                    />
                  </label>
                  <label>
                    <span>确认K数</span>
                    <input
                      type="number"
                      min="1"
                      max="30"
                      value={rule.below_vwap_min_bars}
                      onChange={event => updateTimeStopRule(rule, { below_vwap_min_bars: Number(event.target.value) })}
                    />
                  </label>
                  <label>
                    <span>观察窗口</span>
                    <input
                      type="number"
                      min="1"
                      max="90"
                      value={rule.recent_window_minutes}
                      onChange={event => updateTimeStopRule(rule, { recent_window_minutes: Number(event.target.value) })}
                    />
                  </label>
                  <label>
                    <span>未回封阈值</span>
                    <input
                      type="number"
                      min="0.9"
                      max="1"
                      step="0.001"
                      value={rule.failed_limit_reseal_pct}
                      onChange={event => updateTimeStopRule(rule, { failed_limit_reseal_pct: Number(event.target.value) })}
                    />
                  </label>
                </div>
                <button className="refresh-btn inline" type="button" onClick={() => saveTimeStopRule(rule)}>
                  <Save size={14} />保存规则
                </button>
              </article>
            ))}
          </div>
        </section>
      )}

      {showForm && (
        <div className="pos-form panel">
          <h3>{editingId ? '编辑持仓' : '新增持仓'}</h3>
          <div className="form-grid">
            <input placeholder="代码" value={form.code} onChange={e => setForm(p => ({ ...p, code: e.target.value }))} />
            <input placeholder="名称" value={form.name} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} />
            <input placeholder="数量" type="number" value={form.quantity} onChange={e => setForm(p => ({ ...p, quantity: e.target.value }))} />
            <input placeholder="成本价" type="number" step="0.01" value={form.cost_price} onChange={e => setForm(p => ({ ...p, cost_price: e.target.value }))} />
            <input placeholder="当前价" type="number" step="0.01" value={form.current_price} onChange={e => setForm(p => ({ ...p, current_price: e.target.value }))} />
            <select value={form.position_type} onChange={e => setForm(p => ({ ...p, position_type: e.target.value }))}>
              {['盈利趋势仓', '亏损修复仓', '退出型风险仓'].map(t => <option key={t}>{t}</option>)}
            </select>
            <input placeholder="下一步纪律" value={form.next_discipline} onChange={e => setForm(p => ({ ...p, next_discipline: e.target.value }))} />
          </div>
          <button className="grade-btn" onClick={saveHolding}>保存</button>
        </div>
      )}

      <div className="pos-summary">
        <div className="summary-card">
          <span>总资产</span>
          <strong>{savedTotalAsset ? `${savedTotalAsset.toLocaleString()} 元` : '--'}</strong>
        </div>
        <div className="summary-card">
          <span>可用资金</span>
          <strong style={{ color: cashAvailable >= 0 ? 'var(--ink)' : 'var(--down)' }}>
            {savedTotalAsset ? `${cashAvailable.toLocaleString(undefined, { maximumFractionDigits: 2 })} 元` : '--'}
          </strong>
        </div>
        <div className="summary-card">
          <span>总仓位</span>
          <strong>{savedTotalAsset ? `${(totalPositionRatio * 100).toFixed(1)}%` : '--'}</strong>
        </div>
        <div className="summary-card">
          <span>持仓市值</span>
          <strong>{totalMarketValue.toLocaleString(undefined, { maximumFractionDigits: 2 })} 元</strong>
        </div>
        <div className="summary-card">
          <span>今日盈亏</span>
          <strong style={{ color: totalTodayPnL >= 0 ? 'var(--up)' : 'var(--down)' }}>
            {money(totalTodayPnL)}
          </strong>
        </div>
        <div className="summary-card">
          <span>累计浮盈</span>
          <strong style={{ color: totalPnL >= 0 ? 'var(--up)' : 'var(--down)' }}>
            {money(totalPnL)}
          </strong>
        </div>
      </div>

      {seesaw && (
        <section className="panel">
          <div className="selected-theme-head">
            <div>
              <strong>盘中资金跷跷板监控 · {seesaw.market_mode}</strong>
              <span>{seesaw.summary}</span>
            </div>
          </div>
          <div className="auction-evidence-grid">
            <div>
              <b>行业流入前10名</b>
              <ul>
                {seesaw.inflow_targets.slice(0, 4).map(item => (
                  <li key={item.name}>{item.name}：净流入 {item.net_inflow.toFixed(2)} 亿，主力 {item.main_inflow.toFixed(2)} 亿，涨停 {item.limit_up_count} 只</li>
                ))}
              </ul>
            </div>
            <div>
              <b>行业流出前10名</b>
              <ul>
                {(seesaw.outflow_targets.length ? seesaw.outflow_targets : [{ name: '暂无明显流出板块', net_inflow: 0, main_inflow: 0, limit_up_count: 0 } as RotationItem]).slice(0, 4).map(item => (
                  <li key={item.name}>{item.name}：净流入 {item.net_inflow.toFixed(2)} 亿，主力 {item.main_inflow.toFixed(2)} 亿</li>
                ))}
              </ul>
            </div>
          </div>
          <div className="seesaw-alert-list">
            {highRiskAlerts.length ? highRiskAlerts.slice(0, 6).map(item => (
              <article className={`seesaw-alert-card risk-${item.risk_level}`} key={`${item.code}-${item.sector}`}>
                <div className="seesaw-alert-title">
                  <div>
                    <strong>{item.name}</strong>
                    <small>{item.code} · {item.holding_theme || item.sector || '待确认主线'}</small>
                  </div>
                  <span style={{ color: riskColor(item.risk_level) }}>{item.risk_level}</span>
                </div>
                <div className="seesaw-chip-row">
                  {(item.theme_tags?.length ? item.theme_tags : [item.holding_theme || item.sector]).filter(Boolean).slice(0, 4).map(tag => (
                    <span key={tag}>{tag}</span>
                  ))}
                </div>
                <p className="seesaw-signal">{item.signal}</p>
                <div className="seesaw-facts">
                  <div>
                    <b>主资金曲线</b>
                    <span>
                      {item.flow_basis || '资金流'} · {(item.primary_industry_sector || item.matched_flow_sector || '未匹配')} · 当前 {item.theme_flow_current.toFixed(2)} 亿
                      {item.theme_flow_pullback > 0 ? ` / 回落 ${item.theme_flow_pullback.toFixed(2)} 亿` : ''}
                    </span>
                  </div>
                  <div>
                    <b>个股画像</b>
                    <span>{item.stock_industry || '未抓到行业'} · {(item.stock_concepts || []).slice(0, 4).join('、') || '未抓到概念'}</span>
                  </div>
                  <div>
                    <b>概念辅助</b>
                    <span>{(item.concept_flow_sectors?.length ? item.concept_flow_sectors.slice(0, 3).join('、') : '不参与主曲线')}</span>
                  </div>
                  <div>
                    <b>高点回撤</b>
                    <span>{item.pullback_from_high_pct.toFixed(2)}%</span>
                  </div>
                  <div>
                    <b>分时均价</b>
                    <span>{item.below_vwap ? '已跌破' : '未跌破'}</span>
                  </div>
                  <div>
                    <b>外部吸金</b>
                    <span>{item.external_inflow_target || '同主线内轮动/暂无'}</span>
                  </div>
                </div>
                {item.profit_protection_state && <p>{item.profit_protection_state}</p>}
                <p className="seesaw-advice">{item.advice}</p>
                <div className="trigger-mini-grid">
                  <div><b>板块退潮</b><span>{item.sector_ebb_trigger[0] || '未触发'}</span></div>
                  <div><b>个股弱化</b><span>{item.stock_weakening_trigger[0] || '未触发'}</span></div>
                  <div>
                    <b>利润回撤</b>
                    <span>{item.profit_drawdown_trigger[0] || '未触发'}</span>
                  </div>
                  <div>
                    <b>接回条件</b>
                    <span>{item.buyback_trigger[0] || '等待重新转强'}</span>
                  </div>
                </div>
                <ul>
                  {item.evidence.slice(0, 3).map(line => <li key={line}>{line}</li>)}
                </ul>
              </article>
            )) : (
              <p className="plain-text">暂无持仓触发明显资金抽血风险。</p>
            )}
          </div>
        </section>
      )}

      {executionStates.length > 0 && (
        <section className="panel execution-panel">
          <div className="selected-theme-head">
            <div>
              <strong>持仓执行状态机</strong>
              <span>按利润保护、分时均价、结构止损、板块证据生成动作；缺失数据会标明降级，不用 0 冒充。</span>
            </div>
          </div>
          <div className="execution-card-grid">
            {(urgentExecutions.length ? urgentExecutions : executionStates).slice(0, 6).map(item => (
              <article className="execution-card" key={`${item.code}-${item.updated_at}`}>
                {(() => {
                  const plan = tPlans[item.holding_id]
                  return plan ? (
                    <div className="t-execution-strip">
                      <div><b>{plan.t_type}</b><span>{plan.status}</span></div>
                      <small>计划卖出 {plan.planned_sell_quantity} 股 · 已卖 {plan.actual_sell_quantity} · 已接回 {plan.actual_buyback_quantity}</small>
                      <div className="t-execution-actions">
                        {plan.status === 'planned' && <button type="button" onClick={() => updateTExecution(plan, 'sell')}>记录卖出</button>}
                        {['sold_wait_buyback', 'partially_bought_back'].includes(plan.status) && <button type="button" onClick={() => updateTExecution(plan, 'buyback')}>记录接回</button>}
                        {['sold_wait_buyback', 'partially_bought_back'].includes(plan.status) && <button type="button" onClick={() => updateTExecution(plan, 'reduction')}>转永久减仓</button>}
                      </div>
                    </div>
                  ) : null
                })()}
                <div className="execution-card-head">
                  <div>
                    <strong>{item.name}</strong>
                    <small>{item.code} · {chineseLabel(item.volume_price_state)} · {chineseLabel(item.data_quality)}</small>
                  </div>
                  <span style={{ color: actionColor(item.state) }}>{chineseEvidence(item.recommended_action)}</span>
                </div>
                <div className="execution-line-grid">
                  <div><b>最大浮盈</b><span>{item.profit_snapshot ? `${item.profit_snapshot.maximum_profit_pct.toFixed(2)}%` : '--'}</span></div>
                  <div><b>利润回撤</b><span>{item.profit_snapshot ? `${item.profit_snapshot.profit_drawdown_pct.toFixed(2)}pct` : '--'}</span></div>
                  <div><b>结构止损</b><span>{item.structure_stop_price ? item.structure_stop_price.toFixed(2) : '--'}</span></div>
                  <div><b>止损来源</b><span>{stopSourceLabel(item.stop_source)}</span></div>
                  <div><b>利润保护</b><span>{item.profit_protection_price ? item.profit_protection_price.toFixed(2) : '--'}</span></div>
                  <div><b>建议仓位</b><span>{(item.recommended_position_ratio * 100).toFixed(1)}%</span></div>
                  <div><b>做T</b><span>{item.t_eligible ? item.t_type : '禁止'}</span></div>
                </div>
                <p className="execution-stop-source">{item.stop_source_detail || '止损来源待下一次状态刷新确认。'}</p>
                <div className="execution-evidence">
                  {(item.evidence.length ? item.evidence : ['暂无强触发证据，按原计划观察。']).slice(0, 3).map(line => <p key={line}>{line}</p>)}
                </div>
                <div className="execution-mini-sections">
                  <div>
                    <b>状态迁移</b>
                    {(item.state_history?.length ? item.state_history.slice(0, 3) : []).map(history => (
                      <p key={history.id ?? `${history.captured_at}-${history.new_state}`}>
                        <span>{timeLabel(history.captured_at)}</span>
                        {history.old_state || '初始'} → {history.new_state} · {history.reason}
                      </p>
                    ))}
                    {!item.state_history?.length && <p><span>--</span>等待下一次状态变化</p>}
                  </div>
                  <div>
                    <b>盘中事件</b>
                    {(item.events?.length ? item.events.slice(0, 3) : []).map(event => (
                      <p key={event.id ?? `${event.captured_at}-${event.event_type}`}>
                        <span>{timeLabel(event.captured_at)}</span>
                        {chineseLabel(event.event_type)} · {event.confirmed ? '已确认' : '观察'} · {chineseEvidence(event.evidence?.[0] ?? chineseLabel(event.severity))}
                      </p>
                    ))}
                    {!item.events?.length && <p><span>--</span>暂无新事件</p>}
                  </div>
                  <div>
                    <b>做T口径</b>
                    <p><span>可卖</span>{item.sellable_quantity.toLocaleString()} 股 · 今日买入 {item.today_buy_quantity.toLocaleString()} 股</p>
                    <p><span>类型</span>{item.t_eligible ? item.t_type : '禁止做T'} · {item.t_eligible ? '等待计划生成' : '禁止做T'}</p>
                  </div>
                </div>
                <div className="execution-feedback">
                  <button type="button" onClick={() => createTPlan(item)}>
                    <RefreshCcw size={14} />
                    生成做T计划
                  </button>
                  {['已执行', '部分执行', '暂不执行', '忽略'].map(status => (
                    <button key={status} type="button" onClick={() => sendFeedback(item, status)}>
                      {status === '已执行' ? <CheckCircle2 size={14} /> : <ShieldAlert size={14} />}
                      {status}
                    </button>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {holdingsError ? (
        <div className="panel error-msg"><p>持仓数据加载失败：{holdingsError}。请稍后重试；数据库中的原有记录未因此删除。</p></div>
      ) : holdings.length === 0 ? (
        <div className="panel"><p className="plain-text">暂无持仓，点击"添加持仓"录入。</p></div>
      ) : (
        <div className="pos-table-wrap">
          <table className="pos-table">
            <thead>
              <tr>
                  <th>代码</th><th>名称</th><th>数量</th><th>成本</th><th>现价</th><th>市值</th>
                  <th className="num">盈亏金额</th><th className="num">浮盈%</th><th className="num">今日盈亏</th><th className="num">今日盈亏%</th><th>资金跷跷板</th><th>执行状态</th><th className="num">仓位%</th><th className="num">止损价</th><th className="num">利润保护</th>
                <th>仓位类型</th><th>下一步</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map(h => (
                <tr key={h.id}>
                  {(() => {
                    const alert = seesawByCode.get(h.code)
                    const execution = executionByCode.get(h.code)
                    return (
                      <>
                  <td className="mono">{h.code}</td>
                  <td>{h.name}</td>
                  <td className="num">{h.quantity.toLocaleString()}</td>
                  <td className="num">{h.cost_price.toFixed(2)}</td>
                  <td className="num" title={h.price_note || '手动录入价'}>
                    {h.current_price.toFixed(2)}
                    <span className={`price-dot ${h.price_source === 'realtime' ? 'live' : 'stale'}`} title={h.price_note || '手动录入价'}>●</span>
                  </td>
                  <td className="num">{h.market_value.toLocaleString()}</td>
                  <td className="num" style={{ color: h.profit_amount >= 0 ? 'var(--up)' : 'var(--down)' }}>
                    {h.profit_amount >= 0 ? '+' : ''}{h.profit_amount.toLocaleString()}
                  </td>
                  <td className="num" style={{ color: h.profit_ratio >= 0 ? 'var(--up)' : 'var(--down)' }}>
                    {h.profit_ratio >= 0 ? '+' : ''}{(h.profit_ratio * 100).toFixed(2)}%
                  </td>
                  <td className="num" style={{ color: h.today_profit_amount >= 0 ? 'var(--up)' : 'var(--down)' }} title={h.prev_close ? `昨收 ${h.prev_close.toFixed(2)}，涨跌幅 ${h.change_pct.toFixed(2)}%` : '缺少昨收行情'}>
                    {h.today_profit_amount >= 0 ? '+' : ''}{h.today_profit_amount.toLocaleString()}
                  </td>
                  <td className="num" style={{ color: h.today_profit_ratio >= 0 ? 'var(--up)' : 'var(--down)' }}>
                    {h.today_profit_ratio >= 0 ? '+' : ''}{(h.today_profit_ratio * 100).toFixed(2)}%
                  </td>
                  <td className="seesaw-cell">
                    {alert ? (
                      <div className="seesaw-cell-box">
                        <div className="seesaw-cell-head">
                          <b style={{ color: riskColor(alert.risk_level) }}>{alert.risk_level}</b>
                          <span>{alert.holding_theme || alert.sector || '待确认主线'}</span>
                        </div>
                        <div className="seesaw-cell-meta">
                          <span>主资金曲线：{alert.flow_basis || '资金流'} · {alert.primary_industry_sector || alert.matched_flow_sector || '未匹配'}，当前 {alert.theme_flow_current.toFixed(2)} 亿{alert.theme_flow_pullback > 0 ? `，高位回落 ${alert.theme_flow_pullback.toFixed(2)} 亿` : ''}</span>
                          <span>个股画像：{alert.stock_industry || '未抓到行业'} / {(alert.stock_concepts || []).slice(0, 4).join('、') || '未抓到概念'}</span>
                          <span>概念辅助：{(alert.concept_flow_sectors?.length ? alert.concept_flow_sectors.slice(0, 3).join('、') : '不参与主曲线')}</span>
                          <span>外部吸金：{alert.external_inflow_target || '暂无'}</span>
                        </div>
                        <p>{alert.signal}</p>
                        <p className="seesaw-advice">{alert.advice}</p>
                        <div className="seesaw-trigger-stack">
                          {alert.profit_protection_state && <span>{alert.profit_protection_state}</span>}
                          <span>触发：{alert.trigger_action || alert.stock_weakening_trigger[0] || alert.sector_ebb_trigger[0] || '继续观察'}</span>
                        </div>
                        <small>个股高点回撤 {alert.pullback_from_high_pct.toFixed(2)}% · 主线主力 {alert.sector_main_inflow.toFixed(2)} 亿</small>
                      </div>
                    ) : '--'}
                  </td>
                  <td className="execution-cell">
                    {execution ? (
                      <div className="execution-cell-box">
                        <div className="execution-cell-head">
                          <b style={{ color: actionColor(execution.state) }}>{chineseEvidence(execution.recommended_action)}</b>
                          <span>{chineseLabel(execution.state)}</span>
                        </div>
                        <div className="execution-cell-lines">
                          <span>最大浮盈 {execution.profit_snapshot?.maximum_profit_pct.toFixed(2) ?? '--'}%</span>
                          <span>回撤 {execution.profit_snapshot?.profit_drawdown_pct.toFixed(2) ?? '--'}pct</span>
                          <span>结构 {execution.structure_stop_price.toFixed(2)} / 硬止损 {execution.hard_stop_price.toFixed(2)}</span>
                          <span>来源 {stopSourceLabel(execution.stop_source)}</span>
                          <span>{execution.t_eligible ? `允许${execution.t_type}` : '禁止做T'}</span>
                        </div>
                        <p>{execution.evidence[0] || '按原计划观察。'}</p>
                      </div>
                    ) : '--'}
                  </td>
                  <td className="num">{h.total_asset ? `${(h.position_ratio * 100).toFixed(1)}%` : '--'}</td>
                  <td className="num">{h.stop_loss_price.toFixed(2)}</td>
                  <td className="num">{h.profit_guard_price?.toFixed(2) ?? '--'}</td>
                  <td><span className="type-tag" style={{ color: typeColor(h.position_type), borderColor: typeColor(h.position_type) }}>{h.position_type}</span></td>
                  <td className="discipline-cell">{h.next_discipline || '--'}</td>
                  <td>
                    <div className="table-actions">
                      <button type="button" onClick={() => startEdit(h)} title="编辑">
                        <Pencil size={14} />
                      </button>
                      <button type="button" onClick={() => deleteHolding(h)} title="删除">
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                      </>
                    )
                  })()}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
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
