import { useEffect, useState } from 'react'
import { Activity, AlertTriangle, CheckCircle2, ClipboardCheck, RefreshCcw, RotateCcw, SlidersHorizontal } from 'lucide-react'
import { API_BASE } from '../api'
import { chineseEvidence, chineseLabel } from '../labels'
import type { CalibrationProposal, CalibrationRun, ReviewCalibrationSummary } from '../types'

export default function ReviewCalibration() {
  const [summary, setSummary] = useState<ReviewCalibrationSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')
  const [proposal, setProposal] = useState<CalibrationProposal | null>(null)
  const [calibrationRun, setCalibrationRun] = useState<CalibrationRun | null>(null)

  const load = () => {
    setLoading(true)
    fetch(`${API_BASE}/api/review-calibration/summary`)
      .then(async r => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then(data => {
        setSummary(data)
        setStatus('已刷新')
      })
      .catch(() => setStatus('校准摘要加载失败'))
      .finally(() => setLoading(false))
    fetch(`${API_BASE}/api/reviews/calibration-proposal`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(setProposal)
      .catch(() => setProposal(null))
  }

  const applyCalibration = async () => {
    if (!proposal?.eligible || !window.confirm(`确认应用 ${proposal.changes.length} 项阈值变更？系统会保存变更前快照，可随时回滚。`)) return
    setLoading(true)
    try {
      const response = await fetch(`${API_BASE}/api/reviews/calibration-apply`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation: 'APPLY_CALIBRATION' }),
      })
      if (!response.ok) throw new Error(await response.text())
      setCalibrationRun(await response.json())
      setStatus('参数校准已应用并保存回滚快照')
      load()
    } catch {
      setStatus('参数校准应用失败')
      setLoading(false)
    }
  }

  const rollbackCalibration = async () => {
    if (!calibrationRun || !window.confirm('确认回滚本次参数校准？')) return
    const response = await fetch(`${API_BASE}/api/reviews/calibration-runs/${calibrationRun.id}/rollback`, { method: 'POST' })
    if (response.ok) {
      setCalibrationRun(await response.json())
      setStatus('参数校准已回滚')
      load()
    } else setStatus('参数校准回滚失败')
  }

  useEffect(() => {
    load()
  }, [])

  if (!summary) {
    return (
      <section className="panel">
        <p className="plain-text">{loading ? '加载中...' : status || '暂无复盘校准数据。'}</p>
      </section>
    )
  }

  return (
    <section className="calibration-dashboard">
      <header className="env-hero">
        <div>
          <h2>执行校准概览</h2>
          <p>{summary.focus}</p>
        </div>
        <div className="header-actions">
          <button className="refresh-btn inline" type="button" onClick={() => { window.location.href = `${API_BASE}/api/acceptance/report?download=true` }}><ClipboardCheck size={14} />导出验收报告</button>
          <button className="refresh-btn inline" type="button" onClick={load} disabled={loading}><RefreshCcw size={14} />刷新</button>
        </div>
      </header>

      <div className="calibration-metrics">
        <Metric label="交易复盘" value={`${summary.review_count}/${summary.trade_count}`} />
        <Metric label="平均纪律分" value={`${summary.avg_discipline_score}`} tone={summary.avg_discipline_score >= 70 ? 'good' : 'warn'} />
        <Metric label="计划已校准" value={`${summary.plan_review_count}`} />
        <Metric label="计划缺口" value={`${summary.missing_plan_review_count}`} tone={summary.missing_plan_review_count ? 'bad' : 'good'} />
        <Metric label="执行反馈" value={`${summary.execution_feedback_count}`} />
        <Metric label="忽略提醒" value={`${summary.ignored_recommendation_count}`} tone={summary.ignored_recommendation_count ? 'warn' : 'good'} />
      </div>

      <div className="calibration-grid model-grid">
        <section className="panel calibration-panel">
          <h3><Activity size={16} />模型有效性</h3>
          <div className="model-metric-list">
            {summary.model_metrics.map(item => (
              <article className="model-metric" key={item.key}>
                <div>
                  <b>{item.label}</b>
                  <span>{item.verdict}</span>
                </div>
                <strong>{item.sample_count ? `${item.success_rate.toFixed(1)}%` : '--'}</strong>
                <p>样本 {item.sample_count} · 通过 {item.success_count} · 偏差 {item.fail_count}</p>
                {!!item.average_value && <small>均值 {item.average_value.toFixed(4)}</small>}
                <div className="evidence-inline">
                  {item.evidence.map(evidence => <em key={evidence}>{evidence}</em>)}
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="panel calibration-panel">
          <h3><SlidersHorizontal size={16} />参数建议</h3>
          <div className="suggestion-list">
            {summary.calibration_suggestions.map(item => (
              <article className={`calibration-suggestion level-${item.level}`} key={`${item.target}-${item.suggestion}`}>
                <div>
                  <b>{item.target}</b>
                  <span>{chineseLabel(item.level)}</span>
                </div>
                <p>{item.suggestion}</p>
                <small>{item.reason} · 样本 {item.sample_count}</small>
              </article>
            ))}
            {proposal && (
              <article className={`calibration-suggestion level-${proposal.eligible ? '中' : '观察'}`}>
                <div><b>预期阈值校准方案</b><span>{proposal.sample_count}/{proposal.minimum_samples} 样本</span></div>
                <p>{proposal.rationale}</p>
                {!!proposal.changes.length && <small>拟变更 {proposal.changes.length} 项；应用前保存完整快照，不会静默改写。</small>}
                <div className="header-actions">
                  <button className="refresh-btn inline" type="button" disabled={!proposal.eligible || loading} onClick={applyCalibration}>
                    <SlidersHorizontal size={13} />审阅并应用
                  </button>
                  {calibrationRun?.status === 'applied' && (
                    <button className="refresh-btn inline" type="button" onClick={rollbackCalibration}><RotateCcw size={13} />回滚本次</button>
                  )}
                </div>
              </article>
            )}
          </div>
        </section>
      </div>

      <div className="calibration-grid">
        <section className="panel calibration-panel">
          <h3><AlertTriangle size={16} />校准问题</h3>
          {summary.issues.length ? summary.issues.map(item => (
            <article className={`calibration-issue level-${item.level}`} key={`${item.title}-${item.code}`}>
              <div>
                <b>{item.title}</b>
                <span>{chineseLabel(item.level)}</span>
              </div>
              <p>{item.detail}</p>
              <small>{chineseEvidence(item.action)}</small>
            </article>
          )) : (
            <p className="plain-text">当前没有明显 P1 闭环缺口。</p>
          )}
        </section>

        <section className="panel calibration-panel">
          <h3><ClipboardCheck size={16} />计划偏差样本</h3>
          {summary.recent_plan_deviations.length ? summary.recent_plan_deviations.slice(0, 8).map(item => (
            <article className={`plan-deviation severity-${item.severity}`} key={item.plan_id}>
              <div>
                <b>{item.name}</b>
                <span>{item.severity}</span>
              </div>
              <p>{item.plan_date} · {item.expectation || '未填预期结果'}</p>
              <small>{item.execution || '执行记录缺口'} / {item.deviation || '偏差原因缺口'}</small>
            </article>
          )) : (
            <p className="plain-text">暂无已填写盘后校准的计划卡。</p>
          )}
        </section>

        <section className="panel calibration-panel">
          <h3><CheckCircle2 size={16} />下一步动作</h3>
          <div className="rule-list">
            {summary.next_actions.map(item => <span key={item}>{item}</span>)}
          </div>
          {!!summary.feedback_summary.length && (
            <div className="feedback-summary">
              {summary.feedback_summary.map(item => (
                <span key={item.status}>{item.status} {item.count}</span>
              ))}
            </div>
          )}
        </section>
      </div>
    </section>
  )
}

function Metric({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'neutral' | 'good' | 'warn' | 'bad' }) {
  return (
    <div className={`calibration-metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
