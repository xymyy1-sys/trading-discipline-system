import { useState } from 'react'
import { Play } from 'lucide-react'
import { API_BASE } from '../api'
import { chineseEvidence, chineseLabel } from '../labels'

type Report = { code: string; name: string; trade_date: string; complete: boolean; summary: string[]; checkpoints?: { expected_time: string; expected_signal: string; matched: boolean }[]; frames: { timestamp: string; frame_type: string; state: string; action: string; price: number; vwap: number; data_quality: string; evidence: string[] }[] }

export default function HistoricalReplay() {
  const [code, setCode] = useState('600584')
  const [date, setDate] = useState('2026-07-10')
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(false)
  const run = () => { setLoading(true); fetch(`${API_BASE}/api/replay/${code}?trade_date=${date}`).then(r => r.json()).then(setReport).finally(() => setLoading(false)) }
  return <section className="replay-page"><header className="pos-header"><div><h2>历史事件回放</h2><p>重建预期、量价、事件、状态迁移和操作建议时间线。</p></div><div className="replay-controls"><input value={code} onChange={e => setCode(e.target.value)} /><input type="date" value={date} onChange={e => setDate(e.target.value)} /><button className="grade-btn" onClick={run}><Play size={14} />{loading ? '回放中' : '开始回放'}</button></div></header>
    {report && <><div className={`panel replay-summary ${report.frames.length ? 'complete' : ''}`}><strong>{report.name || report.code} · {report.trade_date}</strong><span>{report.frames.length ? '已重建真实持久化时间线' : '暂无可回放证据'}</span>{report.summary.map(item => <small key={item}>{item}</small>)}</div>
    {!!report.checkpoints?.length && <section className="replay-checkpoints" aria-label="显式验收规则"><strong>显式验收规则</strong>{report.checkpoints.map(item => <span className={item.matched ? 'matched' : ''} key={`${item.expected_time}-${item.expected_signal}`}>{item.expected_time} {chineseLabel(item.expected_signal)} · {item.matched ? '匹配' : '未匹配'}</span>)}</section>}
    <div className="replay-timeline">{report.frames.map((frame, index) => <article key={`${frame.timestamp}-${index}`}><time>{new Date(frame.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}</time><b>{chineseLabel(frame.frame_type)} · {chineseLabel(frame.state)}</b><span>{chineseEvidence(frame.action || frame.evidence[0] || chineseLabel(frame.data_quality))}</span>{frame.price > 0 && <small>价格 {frame.price.toFixed(2)} · 分时均价 {frame.vwap.toFixed(2)}</small>}</article>)}</div></>}
  </section>
}
