import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, ClipboardCheck, RefreshCcw } from 'lucide-react'
import { API_BASE } from '../api'
import type { ReviewCalibrationSummary } from '../types'

export default function ReviewCalibration() {
  const [summary, setSummary] = useState<ReviewCalibrationSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')

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
        <button className="refresh-btn inline" type="button" onClick={load} disabled={loading}>
          <RefreshCcw size={14} />刷新
        </button>
      </header>

      <div className="calibration-metrics">
        <Metric label="交易复盘" value={`${summary.review_count}/${summary.trade_count}`} />
        <Metric label="平均纪律分" value={`${summary.avg_discipline_score}`} tone={summary.avg_discipline_score >= 70 ? 'good' : 'warn'} />
        <Metric label="计划已校准" value={`${summary.plan_review_count}`} />
        <Metric label="计划缺口" value={`${summary.missing_plan_review_count}`} tone={summary.missing_plan_review_count ? 'bad' : 'good'} />
        <Metric label="执行反馈" value={`${summary.execution_feedback_count}`} />
        <Metric label="忽略提醒" value={`${summary.ignored_recommendation_count}`} tone={summary.ignored_recommendation_count ? 'warn' : 'good'} />
      </div>

      <div className="calibration-grid">
        <section className="panel calibration-panel">
          <h3><AlertTriangle size={16} />校准问题</h3>
          {summary.issues.length ? summary.issues.map(item => (
            <article className={`calibration-issue level-${item.level}`} key={`${item.title}-${item.code}`}>
              <div>
                <b>{item.title}</b>
                <span>{item.level}</span>
              </div>
              <p>{item.detail}</p>
              <small>{item.action}</small>
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
