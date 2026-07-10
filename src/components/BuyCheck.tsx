import { useState } from 'react'
import { CheckCircle2, XCircle, AlertTriangle, ArrowRight } from 'lucide-react'
import { API_BASE } from '../api'

type CheckResult = {
  decision: string
  score: number
  allowed_position_ratio: number
  warnings: string[]
  required_actions: string[]
}

const roles = ['龙一', '龙二', '明确前排强势股', '高辨识度容量核心', '补涨股', '后排跟风股', '杂毛/无主线股']

export default function BuyCheck() {
  const [form, setForm] = useState({
    code: '', name: '', market_grade: 'B', position_ratio: '20',
    target_role: '龙二', is_mainline: 'true', has_sector_response: 'true',
    has_volume_price_confirm: 'true', buy_point: '', stop_loss_price: '',
    current_price: '', mode: '标准短线模式',
  })
  const [result, setResult] = useState<CheckResult | null>(null)

  const check = () => {
    fetch(`${API_BASE}/api/checks/pre-trade`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: form.code,
        name: form.name,
        market_grade: form.market_grade,
        position_ratio: Number(form.position_ratio) / 100,
        target_role: form.target_role,
        is_mainline: form.is_mainline === 'true',
        has_sector_response: form.has_sector_response === 'true',
        has_volume_price_confirm: form.has_volume_price_confirm === 'true',
        buy_point: form.buy_point,
        stop_loss_price: Number(form.stop_loss_price) || 0,
        current_price: Number(form.current_price) || 0,
        mode: form.mode,
      }),
    })
      .then(r => r.json())
      .then(setResult)
      .catch(() => {})
  }

  const DecisionIcon = result?.decision?.includes('可买') ? CheckCircle2
    : result?.decision?.includes('确认') ? AlertTriangle
    : XCircle
  const decisionColor = result?.decision?.includes('可买') ? 'var(--up)'
    : result?.decision?.includes('确认') ? 'var(--warn)'
    : 'var(--down)'

  return (
    <div className="buycheck-layout">
      <header className="env-hero">
        <div>
          <h2>买入前检查器</h2>
          <p>输入股票和计划仓位，检查主线、前排、板块响应、量价确认、止损是否可执行、仓位是否合规。</p>
        </div>
      </header>

      <div className="buycheck-grid">
        <div className="panel buycheck-form">
          <h3>检查参数</h3>
          <div className="form-grid">
            <input placeholder="股票代码" value={form.code} onChange={e => setForm(p => ({ ...p, code: e.target.value }))} />
            <input placeholder="股票名称" value={form.name} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} />
            <label>
              市场档位
              <select value={form.market_grade} onChange={e => setForm(p => ({ ...p, market_grade: e.target.value }))}>
                {['A', 'B', 'C', 'D'].map(g => <option key={g}>{g} 档</option>)}
              </select>
            </label>
            <label>
              计划仓位%
              <input type="number" value={form.position_ratio} onChange={e => setForm(p => ({ ...p, position_ratio: e.target.value }))} />
            </label>
            <label>
              标的地位
              <select value={form.target_role} onChange={e => setForm(p => ({ ...p, target_role: e.target.value }))}>
                {roles.map(r => <option key={r}>{r}</option>)}
              </select>
            </label>
            <label>
              交易模式
              <select value={form.mode} onChange={e => setForm(p => ({ ...p, mode: e.target.value }))}>
                <option>标准短线模式</option>
                <option>集中进攻模式</option>
              </select>
            </label>
            <label>
              属于主线
              <select value={form.is_mainline} onChange={e => setForm(p => ({ ...p, is_mainline: e.target.value }))}>
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            </label>
            <label>
              板块有资金响应
              <select value={form.has_sector_response} onChange={e => setForm(p => ({ ...p, has_sector_response: e.target.value }))}>
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            </label>
            <label>
              量价确认
              <select value={form.has_volume_price_confirm} onChange={e => setForm(p => ({ ...p, has_volume_price_confirm: e.target.value }))}>
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            </label>
            <input placeholder="买点类型（突破/回踩/承接）" value={form.buy_point} onChange={e => setForm(p => ({ ...p, buy_point: e.target.value }))} />
            <input placeholder="当前价" type="number" step="0.01" value={form.current_price} onChange={e => setForm(p => ({ ...p, current_price: e.target.value }))} />
            <input placeholder="止损价" type="number" step="0.01" value={form.stop_loss_price} onChange={e => setForm(p => ({ ...p, stop_loss_price: e.target.value }))} />
          </div>
          <button className="check-btn" onClick={check}>
            <ArrowRight size={16} /> 执行检查
          </button>
        </div>

        {result && (
          <div className="panel buycheck-result">
            <h3>检查结果</h3>
            <div className="result-hero" style={{ borderColor: decisionColor }}>
              <span className="result-icon"><DecisionIcon size={28} color={decisionColor} /></span>
              <strong style={{ color: decisionColor, fontSize: 22 }}>{result.decision}</strong>
              <span>评分 {result.score}/100 · 允许仓位 {(result.allowed_position_ratio * 100).toFixed(0)}%</span>
            </div>
            {result.warnings.length > 0 && (
              <div className="result-block warn">
                <h4><AlertTriangle size={14} /> 警告</h4>
                <ul>{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </div>
            )}
            {result.required_actions.length > 0 && (
              <div className="result-block">
                <h4>必须执行</h4>
                <ul>{result.required_actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
