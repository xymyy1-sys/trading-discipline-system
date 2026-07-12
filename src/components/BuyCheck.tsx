import { useState } from 'react'
import { CheckCircle2, XCircle, AlertTriangle, ArrowRight } from 'lucide-react'
import { API_BASE } from '../api'

import type { PreTradeCheckOut as CheckResult } from '../types'

const roles = ['龙一', '龙二', '明确前排强势股', '高辨识度容量核心', '补涨股', '后排跟风股', '杂毛/无主线股']

export default function BuyCheck() {
  const [form, setForm] = useState({
    code: '', name: '', market_grade: 'B', position_ratio: '20',
    target_role: '龙二', is_mainline: 'true', has_sector_response: 'true',
    has_volume_price_confirm: 'true', buy_point: '', stop_loss_price: '',
    current_price: '', mode: '标准短线模式', net_asset: '', risk_ratio: '1', script_limit: '20', market_limit: '50', sector_limit: '40', liquidity_limit: '30',
  })
  const [result, setResult] = useState<CheckResult | null>(null)
  const [riskResult, setRiskResult] = useState<{ risk_budget: number; loss_per_share: number; final_position_value: number; final_position_ratio: number; quantity: number; binding_limit: string; warnings: string[] } | null>(null)

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
    if (Number(form.net_asset) > 0 && Number(form.current_price) > 0 && Number(form.stop_loss_price) > 0) {
      fetch(`${API_BASE}/api/checks/risk-position`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          net_asset: Number(form.net_asset), risk_ratio: Number(form.risk_ratio) / 100,
          entry_price: Number(form.current_price), stop_price: Number(form.stop_loss_price), lot_size: 100,
          script_limit: Number(form.script_limit) / 100, market_limit: Number(form.market_limit) / 100,
          single_stock_limit: Number(form.position_ratio) / 100, sector_limit: Number(form.sector_limit) / 100,
          liquidity_limit: Number(form.liquidity_limit) / 100,
        }),
      }).then(async r => { if (!r.ok) throw new Error(await r.text()); return r.json() }).then(setRiskResult).catch(() => setRiskResult(null))
    }
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
            <input placeholder="账户净资产" type="number" value={form.net_asset} onChange={e => setForm(p => ({ ...p, net_asset: e.target.value }))} />
            <label>单笔风险%<input type="number" step="0.1" value={form.risk_ratio} onChange={e => setForm(p => ({ ...p, risk_ratio: e.target.value }))} /></label>
            <label>剧本上限%<input type="number" value={form.script_limit} onChange={e => setForm(p => ({ ...p, script_limit: e.target.value }))} /></label>
            <label>市场上限%<input type="number" value={form.market_limit} onChange={e => setForm(p => ({ ...p, market_limit: e.target.value }))} /></label>
            <label>板块上限%<input type="number" value={form.sector_limit} onChange={e => setForm(p => ({ ...p, sector_limit: e.target.value }))} /></label>
            <label>流动性上限%<input type="number" value={form.liquidity_limit} onChange={e => setForm(p => ({ ...p, liquidity_limit: e.target.value }))} /></label>
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
        {riskResult && <div className="panel risk-position-result"><h3>风险预算仓位</h3><div className="decision-kpi-grid"><div><b>风险预算</b><span>{riskResult.risk_budget.toFixed(2)}</span></div><div><b>每股风险</b><span>{riskResult.loss_per_share.toFixed(2)}</span></div><div><b>最终数量</b><span>{riskResult.quantity}股</span></div><div><b>仓位金额</b><span>{riskResult.final_position_value.toFixed(2)}</span></div><div><b>仓位比例</b><span>{(riskResult.final_position_ratio * 100).toFixed(1)}%</span></div><div><b>约束来源</b><span>{riskResult.binding_limit}</span></div></div>{riskResult.warnings.map(item => <p className="refresh-note" key={item}>{item}</p>)}</div>}
      </div>
    </div>
  )
}
