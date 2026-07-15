import { useEffect, useState } from 'react'
import { BrainCircuit, RefreshCcw, Send, X } from 'lucide-react'
import { API_BASE } from '../api'
import { usePrivacyMode } from '../privacy-context'

type PositionQaResponse = {
  id: number
  code: string
  question: string
  model: string
  content: string
  status: string
  cached: boolean
  context_as_of: string
  missing_fields: string[]
  updated_at: string
}

const QUICK_QUESTIONS = [
  '这只持仓现在该不该卖？',
  '现在能否加仓？',
  '当前卖出是否属于恐慌割肉？',
  '是否应该等待冲高后分批减仓？',
]

export default function PositionAiAssistant({ code, name }: { code: string; name?: string }) {
  const privacyMode = usePrivacyMode()
  const [open, setOpen] = useState(false)
  const [question, setQuestion] = useState(QUICK_QUESTIONS[0])
  const [answer, setAnswer] = useState<PositionQaResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setAnswer(null)
    setError('')
    setQuestion(QUICK_QUESTIONS[0])
  }, [code])

  const generate = (force: boolean) => {
    if (privacyMode) return
    const cleaned = question.trim()
    if (!code || !cleaned) return
    setLoading(true)
    setError('')
    fetch(`${API_BASE}/api/ai/position-qa/${encodeURIComponent(code)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: cleaned, force }),
    })
      .then(async response => {
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}))
          throw new Error(payload.detail || `AI问答失败（HTTP ${response.status}）`)
        }
        return response.json() as Promise<PositionQaResponse>
      })
      .then(setAnswer)
      .catch(value => setError(value instanceof Error ? value.message : 'AI问答失败'))
      .finally(() => setLoading(false))
  }

  return <>
    <button className="position-ai-trigger" type="button" onClick={() => setOpen(true)} disabled={!code}>
      <BrainCircuit size={16} />问AI：该卖、加仓还是等待？
    </button>
    {open && <div className="position-ai-overlay" onClick={() => setOpen(false)}>
      <section className="position-ai-panel" onClick={event => event.stopPropagation()}>
        <header>
          <div><strong>持仓证据问答</strong><span>{name || code} · {code}</span></div>
          <button type="button" aria-label="关闭" onClick={() => setOpen(false)}><X size={19} /></button>
        </header>

        <div className="position-ai-quick-questions">
          {QUICK_QUESTIONS.map(item => (
            <button key={item} type="button" className={question === item ? 'active' : ''} onClick={() => setQuestion(item)}>{item}</button>
          ))}
        </div>
        <label className="position-ai-question">
          <span>你的问题</span>
          <textarea
            maxLength={500}
            value={question}
            onChange={event => setQuestion(event.target.value)}
            placeholder="例如：长电科技冲高后回落，现在该卖还是等待回踩确认？"
          />
        </label>
        <div className="position-ai-actions">
          <small>{privacyMode
            ? '隐私模式已开启：禁止生成或展示可能包含真实持仓数值的 AI 回答。关闭隐私模式后才可继续。'
            : '为回答问题，真实持仓数量、成本、盈亏及相关证据会发送给外部 DeepSeek；不发送API密钥，也不会自动下单。'}</small>
          <button type="button" onClick={() => generate(false)} disabled={privacyMode || loading || !question.trim()}>
            {loading ? <RefreshCcw size={15} className="spin" /> : <Send size={15} />}
            {answer ? '按当前证据生成' : '生成回答'}
          </button>
        </div>

        {error && <p className="position-ai-error">{error}</p>}
        {loading && <p className="plain-text">正在汇总全市场、外围、板块、预期、分钟量价、执行状态和新闻证据……</p>}
        {answer && privacyMode && (
          <aside className="position-ai-privacy-notice" role="status">
            隐私模式已开启，AI 回答正文已隐藏，页面不会展示其中复述的持仓数量、成本、仓位或盈亏。关闭隐私模式后可重新查看。
          </aside>
        )}
        {answer && !privacyMode && <article className="position-ai-answer">
          <header>
            <div><b>{answer.cached ? '已读取同问题缓存' : '已按当前证据生成'}</b><span>{answer.model}</span></div>
            <time>证据截止 {new Date(answer.context_as_of).toLocaleString('zh-CN')}</time>
          </header>
          <div>{answer.content}</div>
          {answer.missing_fields.length > 0 && <details>
            <summary>本次数据缺口（{answer.missing_fields.length}项）</summary>
            {answer.missing_fields.map(item => <p key={item}>· {item}</p>)}
          </details>}
          <footer>
            <small>回答必须引用证据ID并区分事实/推断；最终交易仍须满足系统执行纪律。</small>
            <button type="button" onClick={() => generate(true)} disabled={loading}><RefreshCcw size={14} />重新生成</button>
          </footer>
        </article>}
      </section>
    </div>}
  </>
}
