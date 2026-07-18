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
  const [outcomeSummary, setOutcomeSummary] = useState<{
    price_outcome_sample_count: number
    calibration_eligible_sample_count: number
    minimum_calibration_samples: number
  } | null>(null)
  const [calibrationRun, setCalibrationRun] = useState<CalibrationRun | null>(null)
  const [dataHealth, setDataHealth] = useState<Array<{ source: string; data_type: string; sample_count: number; missing_rate: number; degraded_count: number; stale_count: number; average_latency_ms: number; latest_status: string; latest_at: string; latest_trade_date: string; trade_date_consistent: boolean; degraded_source: string }>>([])
  const [environmentStats, setEnvironmentStats] = useState<Array<{ market_grade: string; expectation_samples: number; expectation_hit_rate: number; recommendation_samples: number; execution_adoption_rate: number; average_adverse_move: number; data_quality: string }>>([])

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
    fetch(`${API_BASE}/api/reviews/recommendation-outcomes/summary`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => setOutcomeSummary({
        price_outcome_sample_count: Number(data?.price_outcome_sample_count ?? data?.eligible_sample_count) || 0,
        calibration_eligible_sample_count: Number(data?.calibration_eligible_sample_count) || 0,
        minimum_calibration_samples: Number(data?.minimum_calibration_samples) || 0,
      }))
      .catch(() => setOutcomeSummary(null))
    fetch(`${API_BASE}/api/data-quality/health`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => setDataHealth(data.providers || []))
      .catch(() => setDataHealth([]))
    fetch(`${API_BASE}/api/reviews/environment-effectiveness`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => setEnvironmentStats(Array.isArray(data) ? data : []))
      .catch(() => setEnvironmentStats([]))
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

  const reliableOutcomeReady = Boolean(
    outcomeSummary
    && outcomeSummary.minimum_calibration_samples > 0
    && outcomeSummary.calibration_eligible_sample_count >= outcomeSummary.minimum_calibration_samples,
  )

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
        <section className="panel calibration-panel data-health-matrix">
          <h3><Activity size={16} />数据源健康矩阵</h3>
          <p className="plain-text">显示每类真实采集的更新时间、延迟、缺失率、降级状态与交易日一致性；没有留痕的数据源不会显示为健康。</p>
          <div className="health-table-wrap"><table><thead><tr><th>来源 / 数据</th><th>最近更新</th><th>交易日</th><th>延迟</th><th>缺失率</th><th>降级/陈旧</th><th>状态</th></tr></thead><tbody>
            {dataHealth.map(item => <tr key={`${item.source}-${item.data_type}`} className={!item.trade_date_consistent || item.missing_rate >= 20 ? 'health-bad' : item.degraded_count || item.stale_count ? 'health-warn' : ''}>
              <td><b>{item.source}</b><small>{item.data_type}</small></td>
              <td>{new Date(item.latest_at).toLocaleString('zh-CN', { hour12: false })}</td>
              <td>{item.latest_trade_date || '--'}{!item.trade_date_consistent && <em>不一致</em>}</td>
              <td>{item.average_latency_ms.toFixed(0)}ms</td><td>{item.missing_rate.toFixed(1)}%</td>
              <td>{item.degraded_count}/{item.stale_count}{item.degraded_source && <small>{item.degraded_source}</small>}</td>
              <td>{chineseLabel(item.latest_status)} · {item.sample_count}样本</td>
            </tr>)}
            {!dataHealth.length && <tr><td colSpan={7}>暂无数据留痕，不能判定数据源健康。</td></tr>}
          </tbody></table></div>
        </section>
        <section className="panel calibration-panel data-health-matrix">
          <h3><Activity size={16} />按市场环境查看闭环状态</h3>
          <p className="plain-text">这里仅分层展示已有样本和反馈覆盖率，不把小样本的状态符合率解释为模型有效性。</p>
          <div className="health-table-wrap"><table><thead><tr><th>环境</th><th>预期样本</th><th>状态符合率</th><th>建议样本</th><th>反馈采纳率</th><th>平均不利波动</th><th>质量</th></tr></thead><tbody>
            {environmentStats.map(item => <tr key={item.market_grade}>
              <td><b>{item.market_grade}</b></td><td>{item.expectation_samples}</td><td>{item.expectation_hit_rate.toFixed(1)}%</td>
              <td>{item.recommendation_samples}</td><td>{item.execution_adoption_rate.toFixed(1)}%</td><td>{item.average_adverse_move.toFixed(2)}%</td><td>{item.data_quality}</td>
            </tr>)}
            {!environmentStats.length && <tr><td colSpan={7}>暂无足够的跨日环境校准样本。</td></tr>}
          </tbody></table></div>
        </section>
        {reliableOutcomeReady ? <>
          <section className="panel calibration-panel">
            <h3><Activity size={16} />模型有效性</h3>
            <div className="model-metric-list">
              {summary.model_metrics.map(item => (
                <article className="model-metric" key={item.key}>
                  <div><b>{item.label}</b><span>{item.verdict}</span></div>
                  <strong>{item.sample_count ? `${item.success_rate.toFixed(1)}%` : '--'}</strong>
                  <p>样本 {item.sample_count} · 通过 {item.success_count} · 偏差 {item.fail_count}</p>
                  {!!item.average_value && <small>均值 {item.average_value.toFixed(4)}</small>}
                  <div className="evidence-inline">{item.evidence.map(evidence => <em key={evidence}>{evidence}</em>)}</div>
                </article>
              ))}
            </div>
          </section>
          <section className="panel calibration-panel">
            <h3><SlidersHorizontal size={16} />经可靠样本验证的参数建议</h3>
            <div className="suggestion-list">
              {summary.calibration_suggestions.map(item => (
                <article className={`calibration-suggestion level-${item.level}`} key={`${item.target}-${item.suggestion}`}>
                  <div><b>{item.target}</b><span>{chineseLabel(item.level)}</span></div>
                  <p>{item.suggestion}</p><small>{item.reason} · 样本 {item.sample_count}</small>
                </article>
              ))}
              {proposal && <article className="calibration-suggestion level-中">
                <div><b>预期阈值校准方案</b><span>{proposal.sample_count}/{proposal.minimum_samples} 样本</span></div>
                <p>{proposal.rationale}</p>
                {!!proposal.changes.length && <small>拟变更 {proposal.changes.length} 项；应用前保存完整快照，不会静默改写。</small>}
                <div className="header-actions">
                  <button className="refresh-btn inline" type="button" disabled={!proposal.eligible || loading} onClick={applyCalibration}><SlidersHorizontal size={13} />审阅并应用</button>
                  {calibrationRun?.status === 'applied' && <button className="refresh-btn inline" type="button" onClick={rollbackCalibration}><RotateCcw size={13} />回滚本次</button>}
                </div>
              </article>}
            </div>
          </section>
        </> : (
          <section className="panel calibration-panel calibration-closure-diagnostic">
            <h3><AlertTriangle size={16} />结果闭环诊断</h3>
            <p className="plain-text">完整价格结果尚未完成动作方向调整与同标的去相关，因此不计入校准样本；暂不展示“模型有效性”和参数建议，也不会开放参数应用。</p>
            <div className="calibration-metrics compact">
              <Metric label="完整价格结果" value={`${outcomeSummary?.price_outcome_sample_count ?? 0}`} />
              <Metric label="校准合格样本" value={`${outcomeSummary?.calibration_eligible_sample_count ?? 0}`} tone="warn" />
              <Metric label="最低门槛" value={`${outcomeSummary?.minimum_calibration_samples || '--'}`} />
              <Metric label="执行反馈" value={`${summary.execution_feedback_count}`} />
              <Metric label="待补计划复盘" value={`${summary.missing_plan_review_count}`} tone={summary.missing_plan_review_count ? 'bad' : 'good'} />
            </div>
            <p>{proposal?.rationale || '校准方案接口尚未返回可靠样本口径；先补齐建议版本、执行反馈和后续行情结果。'}</p>
          </section>
        )}
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
