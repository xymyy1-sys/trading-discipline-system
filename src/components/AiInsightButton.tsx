import { useEffect, useState } from 'react'
import { BrainCircuit, RefreshCcw, X } from 'lucide-react'
import { API_BASE } from '../api'

type Analysis = { id: number; scope: string; target: string; model: string; content: string; status: string; updated_at: string }

export default function AiInsightButton({ scope, target, label = 'AI深度研判' }: { scope: 'stock' | 'market'; target: string; label?: string }) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setAnalysis(null); setError('')
    if (!target) return
    fetch(`${API_BASE}/api/ai/analysis/${scope}/${encodeURIComponent(target)}`)
      .then(r => r.ok ? r.json() : null).then(value => setAnalysis(value)).catch(() => {})
  }, [scope, target])

  const generate = (force: boolean) => {
    if (!target) return
    setLoading(true); setError(''); setOpen(true)
    fetch(`${API_BASE}/api/ai/analysis/${scope}/${encodeURIComponent(target)}?force=${force}`, { method: 'POST' })
      .then(async r => { if (!r.ok) throw new Error((await r.json()).detail || 'AI分析失败'); return r.json() })
      .then(value => setAnalysis(value))
      .catch(value => setError(value instanceof Error ? value.message : 'AI分析失败'))
      .finally(() => setLoading(false))
  }

  return <>
    <button className="ai-insight-btn" type="button" onClick={() => analysis ? setOpen(true) : generate(false)} disabled={loading || !target}>
      {loading ? <RefreshCcw size={15} className="spin" /> : <BrainCircuit size={15} />}{analysis ? '查看AI研判' : label}
    </button>
    {open && <div className="ai-insight-overlay" onClick={() => setOpen(false)}>
      <section className="ai-insight-panel" onClick={event => event.stopPropagation()}>
        <header><div><strong>AI证据研判</strong><span>{analysis ? `${analysis.model} · ${new Date(analysis.updated_at).toLocaleString('zh-CN')}` : 'gpt-5.6-sol'}</span></div><button type="button" onClick={() => setOpen(false)}><X size={18}/></button></header>
        {loading && <p className="plain-text">正在审查实时证据，通常需要数十秒…</p>}
        {error && <p className="error-msg">{error}</p>}
        {analysis && <div className="ai-insight-content">{analysis.content}</div>}
        <footer><small>AI只审查系统已有证据，不替代交易确认，也不会自动下单。</small><button type="button" onClick={() => generate(true)} disabled={loading}><RefreshCcw size={14}/>重新生成</button></footer>
      </section>
    </div>}
  </>
}
