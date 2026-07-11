import { useEffect, useRef, useState } from 'react'
import { Pencil, Plus, RefreshCcw, Save, Trash2, X } from 'lucide-react'
import { API_BASE } from '../api'

import type {
  HoldingOut as Holding,
  SectorRotationItem as RotationItem,
  MarketSeesaw as SeesawMonitor,
} from '../types'

export default function Positions() {
  const [holdings, setHoldings] = useState<Holding[]>([])
  const [seesaw, setSeesaw] = useState<SeesawMonitor | null>(null)
  const [accountAsset, setAccountAsset] = useState('')
  const [assetSaving, setAssetSaving] = useState(false)
  const [assetMessage, setAssetMessage] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [refreshMessage, setRefreshMessage] = useState('')
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

  const fetchHoldings = () => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    setRefreshing(true)
    setRefreshMessage('')
    fetch(`${API_BASE}/api/holdings`)
      .then(r => r.json())
      .then((data: Holding[]) => applyHoldings(data))
      .then(() => fetchSeesaw())
      .catch(() => setRefreshMessage('行情刷新失败，暂用已有价格'))
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
  }
  useEffect(() => {
    fetchAccountAsset()
    refreshQuotes()
    fetchSeesaw()
    const timer = window.setInterval(() => {
      refreshQuotes()
      fetchSeesaw()
    }, 30000)
    return () => window.clearInterval(timer)
  }, [])

  const totalMarketValue = holdings.reduce((s, h) => s + h.market_value, 0)
  const totalPnL = holdings.reduce((s, h) => s + h.profit_amount, 0)
  const totalTodayPnL = holdings.reduce((s, h) => s + h.today_profit_amount, 0)
  const savedTotalAsset = Number(accountAsset) || holdings.find(h => h.total_asset > 0)?.total_asset || 0
  const cashAvailable = savedTotalAsset ? savedTotalAsset - totalMarketValue : 0
  const totalPositionRatio = savedTotalAsset ? totalMarketValue / savedTotalAsset : 0
  const highRiskAlerts = (seesaw?.holding_alerts ?? []).filter(item => ['高', '中高', '中'].includes(item.risk_level))
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

      <section className="panel account-asset-panel">
        <div>
          <span className="eyebrow">Account Asset</span>
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
      </section>

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
              <b>行业流入 TOP10</b>
              <ul>
                {seesaw.inflow_targets.slice(0, 4).map(item => (
                  <li key={item.name}>{item.name}：净流入 {item.net_inflow.toFixed(2)} 亿，主力 {item.main_inflow.toFixed(2)} 亿，涨停 {item.limit_up_count} 只</li>
                ))}
              </ul>
            </div>
            <div>
              <b>行业流出 TOP10</b>
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
                    <b>VWAP</b>
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

      {holdings.length === 0 ? (
        <div className="panel"><p className="plain-text">暂无持仓，点击"添加持仓"录入。</p></div>
      ) : (
        <div className="pos-table-wrap">
          <table className="pos-table">
            <thead>
              <tr>
                  <th>代码</th><th>名称</th><th>数量</th><th>成本</th><th>现价</th><th>市值</th>
                <th className="num">盈亏金额</th><th className="num">浮盈%</th><th className="num">今日盈亏</th><th className="num">今日盈亏%</th><th>资金跷跷板</th><th className="num">仓位%</th><th className="num">止损价</th><th className="num">利润保护</th>
                <th>仓位类型</th><th>下一步</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map(h => (
                <tr key={h.id}>
                  {(() => {
                    const alert = seesawByCode.get(h.code)
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
