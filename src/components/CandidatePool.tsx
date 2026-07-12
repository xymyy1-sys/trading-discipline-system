import { useEffect, useState } from 'react'
import { RefreshCcw } from 'lucide-react'
import { API_BASE } from '../api'

type Candidate = {
  code: string; name: string; pool: string; score: number; expectation_result: string;
  volume_price_state: string; execution_state: string; data_quality: string;
  reasons: string[]; exclusions: string[];
}

export default function CandidatePool() {
  const [items, setItems] = useState<Candidate[]>([])
  const [loading, setLoading] = useState(false)
  const load = () => {
    setLoading(true)
    fetch(`${API_BASE}/api/candidates`).then(r => r.json()).then(setItems).finally(() => setLoading(false))
  }
  useEffect(load, [])
  return <section className="candidate-pool">
    <header className="pos-header"><div><h2>A/B/C/D 候选池</h2><p>按预期、真实量价、执行风险和数据质量自动分层。</p></div><button className="refresh-btn inline" onClick={load} disabled={loading}><RefreshCcw size={14} />刷新</button></header>
    <div className="candidate-grid">{items.map(item => <article className={`candidate-card pool-${item.pool}`} key={item.code}>
      <div className="candidate-head"><strong>{item.pool}池 · {item.name || item.code}</strong><b>{item.score}</b></div>
      <small>{item.code} · {item.expectation_result} · {item.data_quality}</small>
      {item.reasons.map(reason => <p className="candidate-positive" key={reason}>+ {reason}</p>)}
      {item.exclusions.map(reason => <p className="candidate-negative" key={reason}>- {reason}</p>)}
    </article>)}</div>
    {!items.length && <p className="plain-text">暂无候选数据；先建立次日计划或持仓。</p>}
  </section>
}
