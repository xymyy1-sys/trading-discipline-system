import { buildFlowKineticsView, type FlowKineticsFields } from '../flowKinetics'

export default function FlowKineticsEvidence({
  fields,
  compact = false,
  label = '订单流方向拐点',
}: {
  fields: FlowKineticsFields
  compact?: boolean
  label?: string
}) {
  const view = buildFlowKineticsView(fields)
  return (
    <span className={`flow-kinetics-evidence tone-${view.tone}${compact ? ' compact' : ''}`}>
      <strong>{label}：{view.signal}</strong>
      {view.reliable ? (
        <>
          {view.speed && <span>流速 {view.speed}</span>}
          {view.acceleration && <span>加速度 {view.acceleration}</span>}
          {(view.window || view.asOf) && <small>{[view.window && `窗口 ${view.window}`, view.asOf && `截至 ${view.asOf}`].filter(Boolean).join(' · ')}</small>}
        </>
      ) : (
        <small>{view.asOf ? `仅有一个真实时点（${view.asOf}）` : '真实时点不足，不计算流速、加速度或拐点'}</small>
      )}
    </span>
  )
}
