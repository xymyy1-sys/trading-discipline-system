import { useEffect, useState } from 'react'
import { Plus, RefreshCcw, Trash2 } from 'lucide-react'
import { API_BASE } from '../api'
import { chineseEvidence } from '../labels'
import AiInsightButton from './AiInsightButton'

type Recommendation = {
  code: string; name: string; score: number; tier: string; theme: string; role: string;
  limit_level: number; limit_quality: string; fund_signal: string;
  expectation_status: string; volume_price_status: string; expectation_gap: number | null;
  risk_reward_ratio: number | null; gate_passed: boolean; missing_conditions: string[];
  reasons: string[]; risks: string[]; source: string; category: string; updated_at: string | null;
  entry_reason: string; observation_days: number; converted: boolean;
}

export default function CandidatePool() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [manual, setManual] = useState({ code: '', name: '' })
  const [notice, setNotice] = useState('')
  const load = () => {
    setLoading(true)
    setError('')
    fetch(`${API_BASE}/api/watchlist-recommendations`).then(r => {
        if (!r.ok) throw new Error(`自动观察池请求失败（${r.status}）`)
        return r.json()
      }).then(data => {
        setRecommendations(Array.isArray(data) ? data : [])
        window.dispatchEvent(new CustomEvent('watchlist-updated', { detail: data }))
      }).catch(error => {
        setRecommendations([])
        setError(error instanceof Error ? error.message : '自动观察池行情源不可用')
      }).finally(() => setLoading(false))
  }
  useEffect(load, [])
  const addManual = () => {
    const code = manual.code.trim()
    if (!/^\d{6}$/.test(code)) { setError('请输入6位股票代码'); return }
    setError(''); setNotice('正在加入…')
    fetch(`${API_BASE}/api/watchlist`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...manual, code }) })
      .then(async r => { if (!r.ok) throw new Error((await r.json()).detail || '加入失败'); return r.json() })
      .then(data => { setManual({ code: '', name: '' }); setNotice(`${data.name || code} 已加入观察池`); load() })
      .catch(error => { setNotice(''); setError(error instanceof Error ? error.message : '手动加入观察池失败') })
  }
  const removeItem = (code: string) => {
    const exitReason = window.prompt('请记录剔除原因，便于复盘观察池转化率：', '用户手动剔除') || '用户手动剔除'
    fetch(`${API_BASE}/api/watchlist/${code}?exit_reason=${encodeURIComponent(exitReason)}`, { method: 'DELETE' })
      .then(r => { if (!r.ok) throw new Error('剔除失败'); setNotice('已剔除；今日不会自动递补'); load() })
      .catch(() => setError('剔除观察标的失败'))
  }
  return <section className="candidate-pool">
    <header className="pos-header"><div><h2>自动观察池</h2><p>从主线题材核心股和涨停梯队中自动发现，不再只给已有持仓打分。</p></div><button className="refresh-btn inline" onClick={load} disabled={loading}><RefreshCcw size={14} />{loading ? '分析中' : '重新分析'}</button></header>
    <div className="watchlist-editor panel"><input value={manual.code} onChange={e => setManual(value => ({ ...value, code: e.target.value.replace(/\D/g, '').slice(0, 6) }))} placeholder="6位股票代码"/><input value={manual.name} onChange={e => setManual(value => ({ ...value, name: e.target.value }))} placeholder="股票名称（可选）"/><button type="button" className="grade-btn" onClick={addManual}><Plus size={14}/>加入观察池</button><span>系统每日盘后按当日数据换届10只；剔除后当日不递补，手动加入永久保留。</span></div>
    <p className="entry-discipline-banner">纪律底线：没有盘前计划、交易模式不匹配、直线冲高未回踩，任意一项成立都禁止下单。宁可错过，不做模式外交易。</p>
    {notice && <p className="refresh-note">{notice}</p>}
    {error && <p className="error-msg">{error}；这不是“暂无数据”，请检查网络或行情源。</p>}
    <div className="candidate-grid">{recommendations.map(item => <article className={`candidate-card ${item.tier === '重点观察' ? 'pool-A' : item.tier === '等待确认' ? 'pool-B' : 'pool-D'}`} key={`auto-${item.code}`}>
      <div className="candidate-head"><strong>{item.tier} · {item.name || item.code}</strong><b>{item.score}</b></div>
      <button type="button" className="candidate-remove" onClick={() => removeItem(item.code)} title="从观察池剔除"><Trash2 size={14}/>剔除</button>
      <AiInsightButton scope="stock" target={item.code} />
      <small>{item.code} · {item.theme || '题材待确认'} · {item.role || '角色待确认'}</small>
      <small className="candidate-lifecycle">观察第 {item.observation_days || 1} 天 · {item.entry_reason || '系统评分入选'}{item.converted ? ' · 已转为持仓' : ''}</small>
      {item.category && <p className="candidate-category">{item.category}</p>}
      {item.limit_level > 0 && <p className="candidate-positive">+ {item.limit_level}板 · {item.limit_quality}</p>}
      <div className="candidate-gates">
        <span className="wait">入场纪律：观察池不等于买点；冲高必须等待回踩、量价和板块重新确认</span>
        <span className={item.expectation_gap !== null && item.expectation_gap >= 0 ? 'pass' : 'wait'}>盘前预期：{item.expectation_status}{item.expectation_gap !== null ? `（推演强度差 ${item.expectation_gap.toFixed(1)}）` : ''}</span>
        <span className={!item.missing_conditions.some(value => value.includes('量价')) ? 'pass' : 'wait'}>量价确认：{item.volume_price_status}</span>
        <span className={(item.risk_reward_ratio ?? 0) >= 1.5 ? 'pass' : 'wait'}>风险收益比：{item.risk_reward_ratio?.toFixed(2) ?? '待计算'}</span>
      </div>
      {item.reasons.slice(0, 6).map(reason => <p className="candidate-positive" key={reason}>+ {chineseEvidence(reason)}</p>)}
      {item.risks.slice(0, 3).map(reason => <p className="candidate-negative" key={reason}>- {chineseEvidence(reason)}</p>)}
      {item.missing_conditions.slice(0, 3).map(reason => <p className="candidate-negative" key={`gate-${reason}`}>待补：{reason}</p>)}
    </article>)}</div>
    {!loading && !recommendations.length && !error && <p className="plain-text">当前主线与涨停质量没有产生合格的新观察标的，不用为了凑数量强行入池。</p>}
  </section>
}
