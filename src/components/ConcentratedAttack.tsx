import { useEffect, useState } from 'react'
import { Target, Plus } from 'lucide-react'
import { API_BASE } from '../api'

type ExitCard = {
  id: number
  code: string
  name: string
  max_position_ratio: number
  confirm_price: number
  trim_price: number
  failure_price: number
  outperform_condition: string
  underperform_action: string
  allow_buyback: boolean
  buyback_limit_ratio: number
  mode: string
  created_at: string
}

const exposureLevels = [
  { label: '进攻观察 ≤30%', value: '0.30', desc: '信号初步成立' },
  { label: '集中进攻 30%-60%', value: '0.50', desc: '板块确认 + 个股确认 + 隔日预案' },
  { label: '高暴露 60%-80%', value: '0.75', desc: '板块/个股双确认，失败快速降仓' },
  { label: '极限满仓 80%-100%', value: '0.95', desc: '必须提前写退出卡' },
]

const scripts = [
  { scenario: '超预期', desc: '核心继续强化，板块扩散，持仓放量突破', action: '继续持有，不因普通震荡卖飞' },
  { scenario: '符合预期', desc: '板块仍强但分化，持仓围绕确认位承接', action: '不追加风险，守住确认位可观察' },
  { scenario: '低于预期', desc: '核心走弱、板块分化，持仓跌破确认买点', action: '可卖后至少降低一半暴露' },
  { scenario: '模式失败', desc: '跌破总仓失效线且不能收回', action: '退出剩余进攻仓' },
]

export default function ConcentratedAttack() {
  const [cards, setCards] = useState<ExitCard[]>([])
  const [showForm, setShowForm] = useState(false)
  const [exposure, setExposure] = useState('0.50')
  const [form, setForm] = useState({
    code: '', name: '', max_position_ratio: '0.50', confirm_price: '',
    trim_price: '', failure_price: '', outperform_condition: '', underperform_action: '',
    allow_buyback: 'false', buyback_limit_ratio: '0',
  })

  const fetchCards = () => {
    fetch(`${API_BASE}/api/exit-cards`)
      .then(r => r.json())
      .then(setCards)
      .catch(() => {})
  }
  useEffect(() => { fetchCards() }, [])

  const submit = () => {
    fetch(`${API_BASE}/api/exit-cards`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...form,
        max_position_ratio: Number(form.max_position_ratio),
        confirm_price: Number(form.confirm_price),
        trim_price: Number(form.trim_price),
        failure_price: Number(form.failure_price),
        allow_buyback: form.allow_buyback === 'true',
        buyback_limit_ratio: Number(form.buyback_limit_ratio) || 0,
      }),
    })
      .then(r => r.json())
      .then(() => { setShowForm(false); fetchCards() })
      .catch(() => {})
  }

  return (
    <div className="ca-layout">
      <header className="env-hero">
        <div>
          <h2>集中进攻模式</h2>
          <p>板块共振集中进攻。超过 60% 仓位必须写退出卡。极限满仓前必须区分板块与个股两类证据。</p>
        </div>
      </header>

      <div className="ca-grid">
        <div className="panel ca-rules">
          <h3><Target size={16} /> 暴露等级</h3>
          <div className="exposure-list">
            {exposureLevels.map(l => (
              <button
                key={l.value}
                className={`exp-btn ${exposure === l.value ? 'active' : ''}`}
                onClick={() => { setExposure(l.value); setForm(p => ({ ...p, max_position_ratio: l.value })) }}
              >
                <strong>{l.label}</strong>
                <small>{l.desc}</small>
              </button>
            ))}
          </div>
        </div>

        <div className="panel ca-scripts">
          <h3>隔日剧本</h3>
          <table className="ref-table">
            <thead><tr><th>剧本</th><th>定义</th><th>默认动作</th></tr></thead>
            <tbody>
              {scripts.map(s => (
                <tr key={s.scenario}>
                  <td><strong>{s.scenario}</strong></td>
                  <td>{s.desc}</td>
                  <td>{s.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="panel ca-evidence">
          <h3>两类证据要求</h3>
          <div className="evidence-cols">
            <div>
              <h4>板块证据</h4>
              <ul>
                <li>情绪核心涨停</li>
                <li>梯队完整</li>
                <li>板块强于指数</li>
              </ul>
            </div>
            <div>
              <h4>个股证据</h4>
              <ul>
                <li>突破关键确认位</li>
                <li>承接有效</li>
                <li>强于板块至少不弱</li>
              </ul>
            </div>
          </div>
          <p className="plain-text">核心涨停只证明板块情绪增强，不自动证明跟随股适合满仓。</p>
        </div>
      </div>

      <div className="ca-exit-section">
        <div className="pos-header">
          <h3>退出卡记录</h3>
          <button className="grade-btn" onClick={() => setShowForm(!showForm)}>
            <Plus size={16} /> 填写退出卡
          </button>
        </div>

        {showForm && (
          <div className="panel ca-form">
            <h3>新退出卡</h3>
            <div className="form-grid">
              <input placeholder="代码" value={form.code} onChange={e => setForm(p => ({ ...p, code: e.target.value }))} />
              <input placeholder="名称" value={form.name} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} />
              <input placeholder="确认位价格" type="number" step="0.01" value={form.confirm_price} onChange={e => setForm(p => ({ ...p, confirm_price: e.target.value }))} />
              <input placeholder="减仓位价格" type="number" step="0.01" value={form.trim_price} onChange={e => setForm(p => ({ ...p, trim_price: e.target.value }))} />
              <input placeholder="失效价格" type="number" step="0.01" value={form.failure_price} onChange={e => setForm(p => ({ ...p, failure_price: e.target.value }))} />
              <input placeholder="超预期持有条件" value={form.outperform_condition} onChange={e => setForm(p => ({ ...p, outperform_condition: e.target.value }))} />
              <input placeholder="低于预期处理动作" value={form.underperform_action} onChange={e => setForm(p => ({ ...p, underperform_action: e.target.value }))} />
              <label>
                允许买回
                <select value={form.allow_buyback} onChange={e => setForm(p => ({ ...p, allow_buyback: e.target.value }))}>
                  <option value="false">否</option>
                  <option value="true">是</option>
                </select>
              </label>
              <button className="grade-btn" onClick={submit}>保存退出卡</button>
            </div>
          </div>
        )}

        {cards.length === 0 ? (
          <div className="panel"><p className="plain-text">暂无退出卡记录。在集中进攻仓位超过 60% 之前必须填写。</p></div>
        ) : (
          <div className="pos-table-wrap">
            <table className="pos-table">
              <thead>
                <tr>
                  <th>代码</th><th>名称</th><th>最高仓%</th><th>确认位</th><th>减仓位</th>
                  <th>失效位</th><th>超预期条件</th><th>低于预期</th><th>买回</th>
                </tr>
              </thead>
              <tbody>
                {cards.map(c => (
                  <tr key={c.id}>
                    <td className="mono">{c.code}</td>
                    <td>{c.name}</td>
                    <td className="num">{(c.max_position_ratio * 100).toFixed(0)}%</td>
                    <td className="num">{c.confirm_price.toFixed(2)}</td>
                    <td className="num">{c.trim_price.toFixed(2)}</td>
                    <td className="num" style={{ color: 'var(--down)' }}>{c.failure_price.toFixed(2)}</td>
                    <td className="reason-cell">{c.outperform_condition}</td>
                    <td className="reason-cell">{c.underperform_action}</td>
                    <td>{c.allow_buyback ? `是 · 上限${(c.buyback_limit_ratio * 100).toFixed(0)}%` : '否'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
