import { useEffect, useState } from 'react'
import { RefreshCcw } from 'lucide-react'
import { API_BASE } from '../api'
import { chineseEvidence, chineseLabel } from '../labels'

type Candidate = {
  code: string; name: string; pool: string; score: number; expectation_result: string;
  volume_price_state: string; execution_state: string; data_quality: string;
  reasons: string[]; exclusions: string[];
}

type Recommendation = {
  code: string; name: string; score: number; tier: string; theme: string; role: string;
  limit_level: number; limit_quality: string; fund_signal: string;
  expectation_status: string; volume_price_status: string; expectation_gap: number | null;
  risk_reward_ratio: number | null; gate_passed: boolean; missing_conditions: string[];
  reasons: string[]; risks: string[]; source: string; updated_at: string | null;
}

export default function CandidatePool() {
  const [items, setItems] = useState<Candidate[]>([])
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const load = () => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      fetch(`${API_BASE}/api/watchlist-recommendations`).then(r => {
        if (!r.ok) throw new Error(`自动观察池请求失败（${r.status}）`)
        return r.json()
      }),
      fetch(`${API_BASE}/api/candidates`).then(r => {
        if (!r.ok) throw new Error(`已有标的分层请求失败（${r.status}）`)
        return r.json()
      }),
    ]).then(([recommendationResult, candidateResult]) => {
      if (recommendationResult.status === 'fulfilled') {
        setRecommendations(Array.isArray(recommendationResult.value) ? recommendationResult.value : [])
      } else {
        setRecommendations([])
        setError(recommendationResult.reason instanceof Error ? recommendationResult.reason.message : '自动观察池行情源不可用')
      }
      if (candidateResult.status === 'fulfilled') {
        setItems(Array.isArray(candidateResult.value) ? candidateResult.value : [])
      } else {
        setItems([])
        setError(previous => [previous, candidateResult.reason instanceof Error ? candidateResult.reason.message : '已有标的加载失败'].filter(Boolean).join('；'))
      }
    }).finally(() => setLoading(false))
  }
  useEffect(load, [])
  return <section className="candidate-pool">
    <header className="pos-header"><div><h2>自动观察池</h2><p>从主线题材核心股和涨停梯队中自动发现，不再只给已有持仓打分。</p></div><button className="refresh-btn inline" onClick={load} disabled={loading}><RefreshCcw size={14} />{loading ? '分析中' : '重新分析'}</button></header>
    {error && <p className="error-msg">{error}；这不是“暂无数据”，请检查网络或行情源。</p>}
    <div className="candidate-grid">{recommendations.map(item => <article className={`candidate-card ${item.tier === '重点观察' ? 'pool-A' : item.tier === '等待确认' ? 'pool-B' : 'pool-D'}`} key={`auto-${item.code}`}>
      <div className="candidate-head"><strong>{item.tier} · {item.name || item.code}</strong><b>{item.score}</b></div>
      <small>{item.code} · {item.theme || '题材待确认'} · {item.role || '角色待确认'}</small>
      {item.limit_level > 0 && <p className="candidate-positive">+ {item.limit_level}板 · {item.limit_quality}</p>}
      <div className="candidate-gates">
        <span className={item.expectation_gap !== null && item.expectation_gap >= 0 ? 'pass' : 'wait'}>盘前预期：{item.expectation_status}{item.expectation_gap !== null ? `（推演强度差 ${item.expectation_gap.toFixed(1)}）` : ''}</span>
        <span className={!item.missing_conditions.some(value => value.includes('量价')) ? 'pass' : 'wait'}>量价确认：{item.volume_price_status}</span>
        <span className={(item.risk_reward_ratio ?? 0) >= 1.5 ? 'pass' : 'wait'}>风险收益比：{item.risk_reward_ratio?.toFixed(2) ?? '待计算'}</span>
      </div>
      {item.reasons.slice(0, 6).map(reason => <p className="candidate-positive" key={reason}>+ {chineseEvidence(reason)}</p>)}
      {item.risks.slice(0, 3).map(reason => <p className="candidate-negative" key={reason}>- {chineseEvidence(reason)}</p>)}
      {item.missing_conditions.slice(0, 3).map(reason => <p className="candidate-negative" key={`gate-${reason}`}>待补：{reason}</p>)}
    </article>)}</div>
    {!loading && !recommendations.length && !error && <p className="plain-text">当前主线与涨停质量没有产生合格的新观察标的，不用为了凑数量强行入池。</p>}
    <header className="pos-header candidate-existing-head"><div><h3>已有标的纪律分层</h3><p>仅用于管理持仓和已建立的次日计划，不再冒充自动选股结果。</p></div></header>
    <div className="candidate-grid">{items.map(item => <article className={`candidate-card pool-${item.pool}`} key={item.code}>
      <div className="candidate-head"><strong>{item.pool}池 · {item.name || item.code}</strong><b>{item.score}</b></div>
      <small>{item.code} · {chineseLabel(item.expectation_result)} · {chineseLabel(item.data_quality)}</small>
      {item.reasons.map(reason => <p className="candidate-positive" key={reason}>+ {chineseEvidence(reason)}</p>)}
      {item.exclusions.map(reason => <p className="candidate-negative" key={reason}>- {chineseEvidence(reason)}</p>)}
    </article>)}</div>
    {!loading && !items.length && !error && <p className="plain-text">暂无已有标的。</p>}
  </section>
}
