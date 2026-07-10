import { Fragment, useEffect, useState } from 'react'
import { Pencil, Plus, Trash2, X } from 'lucide-react'
import { API_BASE } from '../api'

type Trade = {
  id: number
  code: string
  name: string
  traded_at: string
  side: string
  price: number
  quantity: number
  amount: number
  total_asset: number
  position_ratio: number
  cost_price: number
  stop_loss_price: number
  reason: string
  mode: string
  compliant: boolean
  human_tags: string[]
  review?: TradeReview | null
}

type TradeReview = {
  id: number
  trade_id: number
  verdict: string
  status: string
  discipline_score: number
  summary: string
  stock_context: string
  sector_context: string
  market_context: string
  error_message: string
  mistakes: string[]
  avoid_actions: string[]
  weakness_tags: string[]
  created_at: string
}

type GrowthProfile = {
  trade_count: number
  review_count: number
  dominant_weaknesses: string[]
  frequent_mistakes: string[]
  current_focus: string
  improvement_actions: string[]
  recent_scores: number[]
}

const humanTagOptions = ['贪婪', '恐惧', '幻想', '侥幸', '死扛', '冲动追高', '不甘心', '扳本', '卖飞后悔']

export default function TradeLog() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [profile, setProfile] = useState<GrowthProfile | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState({
    code: '', name: '', side: '买入', price: '', quantity: '',
    total_asset: '', cost_price: '', reason: '', mode: '标准短线模式',
    compliant: 'true', tags: [] as string[],
  })

  const fetchTrades = () => {
    fetch(`${API_BASE}/api/trades`)
      .then(r => r.json())
      .then(setTrades)
      .catch(() => {})
    fetch(`${API_BASE}/api/trade-growth-profile`)
      .then(r => r.json())
      .then(setProfile)
      .catch(() => {})
  }
  useEffect(() => { fetchTrades() }, [])

  const emptyForm = () => ({
    code: '', name: '', side: '买入', price: '', quantity: '',
    total_asset: '', cost_price: '', reason: '', mode: '标准短线模式',
    compliant: 'true', tags: [] as string[],
  })

  const resetForm = () => {
    setForm(emptyForm())
    setEditingId(null)
    setShowForm(false)
    setError('')
  }

  const submit = () => {
    setSaving(true)
    setError('')
    fetch(editingId ? `${API_BASE}/api/trades/${editingId}` : `${API_BASE}/api/trades`, {
      method: editingId ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: form.code, name: form.name, side: form.side,
        price: Number(form.price), quantity: Number(form.quantity),
        total_asset: Number(form.total_asset), cost_price: Number(form.cost_price),
        reason: form.reason, mode: form.mode,
        compliant: form.compliant === 'true',
        human_tags: form.tags,
      }),
    })
      .then(async r => {
        if (!r.ok) {
          const text = await r.text()
          throw new Error(text || `保存失败：HTTP ${r.status}`)
        }
        return r.json()
      })
      .then(() => {
        resetForm()
        fetchTrades()
      })
      .catch(err => setError(err instanceof Error ? err.message : '保存失败'))
      .finally(() => setSaving(false))
  }

  const toggleTag = (tag: string) => {
    setForm(p => ({
      ...p,
      tags: p.tags.includes(tag) ? p.tags.filter(t => t !== tag) : [...p.tags, tag],
    }))
  }

  const startEdit = (trade: Trade) => {
    setEditingId(trade.id)
    setShowForm(true)
    setError('')
    setForm({
      code: trade.code,
      name: trade.name,
      side: trade.side,
      price: String(trade.price),
      quantity: String(trade.quantity),
      total_asset: String(trade.total_asset),
      cost_price: String(trade.cost_price),
      reason: trade.reason,
      mode: trade.mode,
      compliant: String(trade.compliant),
      tags: trade.human_tags,
    })
  }

  const deleteTrade = (trade: Trade) => {
    if (!window.confirm(`确认删除 ${trade.name} 的这笔交易记录？对应自动复盘也会删除。`)) return
    fetch(`${API_BASE}/api/trades/${trade.id}`, { method: 'DELETE' })
      .then(() => fetchTrades())
      .catch(() => {})
  }

  const clearTrades = () => {
    if (!window.confirm('确认清空所有交易记录？对应自动复盘和成长画像样本也会清空。')) return
    setClearing(true)
    fetch(`${API_BASE}/api/trades`, { method: 'DELETE' })
      .then(() => {
        resetForm()
        fetchTrades()
      })
      .catch(() => {})
      .finally(() => setClearing(false))
  }

  const classify = (trade: Trade) => {
    const review = trade.review
    if (review?.status === 'pending') return { label: '待深度复盘', cls: 'tag-yellow' }
    if (!trade.compliant) return { label: trade.side.includes('卖') || trade.side.includes('减') ? '体系外卖出待核对' : '体系外冲动买入', cls: 'tag-red' }
    if (review?.verdict === '明显偏离') return { label: '纪律偏离', cls: 'tag-red' }
    if (review?.verdict === '存疑') return { label: trade.side.includes('卖') || trade.side.includes('减') ? '卖出纪律待核对' : '体系内存疑', cls: 'tag-yellow' }
    if (trade.position_ratio > 0.4 && !trade.mode.includes('集中')) return { label: '高仓位风险', cls: 'tag-yellow' }
    if (trade.side === '买入' || trade.side === '加仓') return { label: '体系内买入', cls: 'tag-green' }
    if (trade.side === '做T') return { label: '做T纪律待核对', cls: 'tag-blue' }
    return { label: '体系内卖出', cls: 'tag-green' }
  }

  return (
    <div className="log-layout">
      <header className="pos-header">
        <div>
          <h2>交易日志</h2>
          <p>记录每笔买入、卖出、加仓、减仓、做T、计划变形，自动分类并标记人性弱点。</p>
        </div>
        <button className="grade-btn" onClick={() => showForm ? resetForm() : setShowForm(true)}>
          {showForm ? <X size={16} /> : <Plus size={16} />}
          {showForm ? '取消' : '记录交易'}
        </button>
      </header>

      {profile && (
        <section className="growth-panel panel">
          <div>
            <span className="eyebrow">Growth Profile</span>
            <h3>成长画像</h3>
            <p>{profile.current_focus}</p>
          </div>
          <div className="growth-metrics">
            <span>交易 <strong>{profile.trade_count}</strong></span>
            <span>复盘 <strong>{profile.review_count}</strong></span>
            <span>均分 <strong>{avg(profile.recent_scores)}</strong></span>
          </div>
          <div className="growth-tags">
            {[...profile.dominant_weaknesses, ...profile.frequent_mistakes.slice(0, 2)].map(item => (
              <b key={item}>{item}</b>
            ))}
            {!profile.dominant_weaknesses.length && !profile.frequent_mistakes.length && <span>暂无高频问题</span>}
          </div>
          <div className="rule-list compact">
            {profile.improvement_actions.slice(0, 3).map(item => <span key={item}>{item}</span>)}
          </div>
        </section>
      )}

      {showForm && (
        <div className="panel log-form">
          <h3>{editingId ? '编辑交易记录' : '新交易记录'}</h3>
          <div className="form-grid">
            <input placeholder="代码" value={form.code} onChange={e => setForm(p => ({ ...p, code: e.target.value }))} />
            <input placeholder="名称" value={form.name} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} />
            <select value={form.side} onChange={e => setForm(p => ({ ...p, side: e.target.value }))}>
              {['买入', '卖出', '加仓', '减仓', '做T'].map(s => <option key={s}>{s}</option>)}
            </select>
            <input placeholder="价格" type="number" step="0.01" value={form.price} onChange={e => setForm(p => ({ ...p, price: e.target.value }))} />
            <input placeholder="数量" type="number" value={form.quantity} onChange={e => setForm(p => ({ ...p, quantity: e.target.value }))} />
            <input placeholder="总资产" type="number" value={form.total_asset} onChange={e => setForm(p => ({ ...p, total_asset: e.target.value }))} />
            <input placeholder="成本价" type="number" step="0.01" value={form.cost_price} onChange={e => setForm(p => ({ ...p, cost_price: e.target.value }))} />
            <input placeholder="交易理由" value={form.reason} onChange={e => setForm(p => ({ ...p, reason: e.target.value }))} />
            <select value={form.mode} onChange={e => setForm(p => ({ ...p, mode: e.target.value }))}>
              {['标准短线模式', '集中进攻模式'].map(m => <option key={m}>{m}</option>)}
            </select>
            <select value={form.compliant} onChange={e => setForm(p => ({ ...p, compliant: e.target.value }))}>
              <option value="true">符合体系</option>
              <option value="false">违反体系</option>
            </select>
          </div>
          <div className="tag-select">
            <span>人性弱点：</span>
            {humanTagOptions.map(t => (
              <button key={t} className={`tag-btn ${form.tags.includes(t) ? 'on' : ''}`} onClick={() => toggleTag(t)}>
                {t}
              </button>
            ))}
          </div>
          {error && <p className="form-error">{error}</p>}
          <button className="grade-btn" onClick={submit} disabled={saving}>
            {saving ? '保存中' : editingId ? '保存修改' : '保存交易'}
          </button>
        </div>
      )}

      {trades.length === 0 ? (
        <div className="panel"><p className="plain-text">暂无交易记录。</p></div>
      ) : (
        <>
          <div className="table-toolbar">
            <button className="danger-btn" onClick={clearTrades} disabled={clearing}>
              <Trash2 size={15} /> {clearing ? '清空中' : '清空交易记录'}
            </button>
          </div>
          <div className="pos-table-wrap">
            <table className="pos-table">
              <thead>
                <tr>
                  <th>时间</th><th>代码</th><th>方向</th><th className="num">价格</th><th className="num">数量</th>
                  <th className="num">金额</th><th className="num">本笔仓位%</th><th>模式</th><th>分类</th><th>弱点</th><th>理由</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                {trades.map(t => {
                  const cl = classify(t)
                  return (
                    <Fragment key={t.id}>
                      <tr key={t.id}>
                        <td className="mono">{new Date(t.traded_at).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</td>
                        <td className="mono">{t.code}</td>
                        <td style={{ color: t.side === '买入' || t.side === '加仓' ? 'var(--up)' : 'var(--down)' }}>{t.side}</td>
                        <td className="num">{t.price.toFixed(2)}</td>
                        <td className="num">{t.quantity.toLocaleString()}</td>
                        <td className="num">{t.amount.toLocaleString()}</td>
                        <td className="num">{t.total_asset ? `${(t.position_ratio * 100).toFixed(1)}%` : '--'}</td>
                        <td>{t.mode.replace('标准短线模式', '标准').replace('集中进攻模式', '集中进攻')}</td>
                        <td><span className={`class-tag ${cl.cls}`}>{cl.label}</span></td>
                        <td className="tags-cell">{t.human_tags.map(tg => <span key={tg} className="weak-tag">{tg}</span>)}</td>
                        <td className="reason-cell">{t.reason}</td>
                        <td>
                          <div className="table-actions">
                            <button type="button" onClick={() => startEdit(t)} title="编辑">
                              <Pencil size={14} />
                            </button>
                            <button type="button" onClick={() => deleteTrade(t)} title="删除">
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                      {t.review && (
                        <tr key={`${t.id}-review`} className="review-row">
                          <td colSpan={12}>
                            <TradeReviewCard review={t.review} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

function TradeReviewCard({ review }: { review: TradeReview }) {
  const pending = review.status === 'pending'
  const failed = review.status === 'failed'
  return (
    <div className={`trade-review-card ${pending ? 'is-pending' : ''}`}>
      <div className="trade-review-head">
        <strong>{review.verdict}</strong>
        <span>{pending ? '生成中' : failed ? '数据缺口' : `纪律分 ${review.discipline_score}`}</span>
      </div>
      <p>{review.summary}</p>
      {review.error_message && <p className="form-error">{review.error_message}</p>}
      <div className="review-context-grid">
        <span>{review.market_context}</span>
        <span>{review.sector_context}</span>
        <span>{review.stock_context}</span>
      </div>
      <div className="review-chip-row">
        {review.weakness_tags.map(item => <b key={item}>{item}</b>)}
        {review.mistakes.slice(0, 3).map(item => <span key={item}>{item}</span>)}
      </div>
      <div className="rule-list compact">
        {review.avoid_actions.slice(0, 3).map(item => <span key={item}>{item}</span>)}
      </div>
    </div>
  )
}

function avg(values: number[]) {
  if (!values.length) return '--'
  return Math.round(values.reduce((sum, item) => sum + item, 0) / values.length)
}
