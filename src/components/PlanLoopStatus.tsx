import { ArrowRight, CheckCircle2, Clock3, RotateCcw, ShieldAlert } from 'lucide-react'
import { chineseEvidence } from '../labels'
import type { AuctionPlan, PlanAdviceChange, PlanAdviceLevel } from '../types'

type PlanLoopStatusProps = {
  plan: AuctionPlan | null | undefined
  planDate?: string
  compact?: boolean
}

const levelLabels: Record<PlanAdviceLevel, string> = {
  observe: '观察',
  positive: '正向确认',
  warning: '风险警告',
  critical: '严重风险',
}

const changeLabels: Record<PlanAdviceChange, string> = {
  initialized: '初次生成',
  unchanged: '继续有效',
  upgraded: '建议已升级',
  downgraded: '建议已降级',
  withdrawn: '原建议已撤销',
  replaced: '建议已替换',
}

export default function PlanLoopStatus({ plan, planDate, compact = false }: PlanLoopStatusProps) {
  if (!plan) {
    if (!compact) return null
    return (
      <article className="plan-loop-card compact level-observe">
        <header><div><h4>计划执行闭环</h4><span>计划待建立</span></div><strong>数据缺口</strong></header>
        <p><b>尚无当前计划</b> · 盘后剧本未生成或当前交易日计划尚未同步。</p>
        <small>不使用旧计划替代；等待后台盘后生成，或到“持仓次日计划”核验。</small>
      </article>
    )
  }
  const currentAdvice = plan.current_advice || plan.operation_advice || plan.stage_decision
  const branchLabel = plan.selected_branch_label || fallbackBranchLabel(plan.selected_branch)
  const branchStatus = plan.branch_status === 'active' ? '已自动选中' : '等待竞价选择'
  const adviceLevel = plan.advice_level || 'observe'
  const change = plan.advice_change || 'initialized'
  const stages = plan.stage_checks ?? []
  const refreshedAt = plan.auto_refreshed_at || plan.refreshed_at

  if (compact) {
    return (
      <article className={`plan-loop-card compact level-${adviceLevel}`}>
        <header>
          <div><h4>计划执行闭环</h4><span>{planDate ? `${planDate} · ` : ''}{branchStatus} · V{plan.advice_revision ?? 1}</span></div>
          <strong>{levelLabels[adviceLevel]}</strong>
        </header>
        <p><b>{branchLabel}</b> · {currentAdvice || '等待竞价、开盘与分钟量价共同确认。'}</p>
        <small>{plan.branch_reason || plan.advice_change_reason || '盘后剧本已建立，等待下一阶段真实证据。'}</small>
        {!!plan.previous_advice && change !== 'unchanged' && (
          <p className="plan-loop-change"><RotateCcw size={13} />{changeLabels[change]}：{plan.advice_change_reason || '证据变化触发自动修正。'}</p>
        )}
      </article>
    )
  }

  return (
    <section className={`plan-loop-card level-${adviceLevel}`}>
      <header>
        <div>
          <span className="plan-loop-eyebrow">盘后剧本 → 竞价选支 → 开盘验证 → 动态修正</span>
          <h3>自动计划执行闭环</h3>
        </div>
        <div className="plan-loop-badges">
          <strong>{levelLabels[adviceLevel]}</strong>
          <span>{branchStatus}</span>
          {planDate && <span>计划日 {planDate}</span>}
          <span>建议 V{plan.advice_revision ?? 1}</span>
        </div>
      </header>
      <div className="plan-loop-current">
        <div>
          <span>当前自动分支</span>
          <b>{branchLabel}</b>
          <p>{plan.branch_reason || '等待集合竞价数据后，从低开下杀、平开震荡和高开冲高中自动选择。'}</p>
        </div>
        <ArrowRight size={20} aria-hidden="true" />
        <div>
          <span>当前建议</span>
          <b>{currentAdvice || '等待下一阶段验证'}</b>
          <p>{plan.advice_change_reason || '建议会随开盘和分钟量价证据自动撤销、降级或升级。'}</p>
        </div>
      </div>
      <div className={`plan-loop-change change-${change}`}>
        {change === 'upgraded' || adviceLevel === 'critical' ? <ShieldAlert size={15} /> : change === 'unchanged' ? <CheckCircle2 size={15} /> : <RotateCcw size={15} />}
        <b>{changeLabels[change]}</b>
        {plan.previous_advice && change !== 'unchanged' && <span>原建议：{plan.previous_advice}</span>}
        <span>{plan.advice_change_reason || '当前证据没有改变原计划。'}</span>
        {refreshedAt && <time><Clock3 size={13} />{formatTime(refreshedAt)}</time>}
      </div>
      {!!stages.length && (
        <div className="plan-loop-stages" aria-label="自动闭环阶段链">
          {stages.map((item, index) => (
            <article key={`${item.stage}-${index}`} className={`stage-${stageTone(item.status)}`}>
              <div><span>{index + 1}</span><b>{item.stage}</b><strong>{item.status}</strong></div>
              <p>{item.decision || item.trigger}</p>
              <small>{item.required_action}</small>
            </article>
          ))}
        </div>
      )}
      {!!plan.advice_history?.length && (
        <details className="plan-loop-history">
          <summary>查看建议版本历史（{plan.advice_history.length}）</summary>
          {[...plan.advice_history].reverse().slice(0, 8).map(item => (
            <div key={`${item.revision}-${item.created_at}`} className={`history-${item.state}`}>
              <time>{formatTime(item.created_at)}</time>
              <b>V{item.revision} · {levelLabels[item.level] || item.level}</b>
              <span>{chineseEvidence(item.advice)}</span>
              <small>{item.reason}{item.withdraw_reason ? `；撤销：${item.withdraw_reason}` : ''}</small>
            </div>
          ))}
        </details>
      )}
    </section>
  )
}

function fallbackBranchLabel(branch?: string) {
  if (branch === 'low_open_selloff') return '低开下杀剧本'
  if (branch === 'range_open_balance') return '平开震荡剧本'
  if (branch === 'high_open_rally') return '高开冲高剧本'
  if (branch === 'data_gap') return '数据缺口保护分支'
  return '等待竞价自动选支'
}

function stageTone(status: string) {
  const value = String(status || '').toLowerCase()
  if (value.includes('失败') || value.includes('证伪') || value.includes('invalid') || value.includes('failed')) return 'risk'
  if (value.includes('通过') || value.includes('确认') || value.includes('passed') || value.includes('confirmed')) return 'passed'
  if (value.includes('观察') || value.includes('等待') || value.includes('pending') || value.includes('watch')) return 'watch'
  return 'neutral'
}

function formatTime(value: string) {
  const parsed = new Date(value.includes('T') ? value : value.replace(' ', 'T'))
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
