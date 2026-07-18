import { chineseEvidence, chineseLabel } from '../labels'
import { SensitiveEvidenceText } from '../privacy'

type DecisionBasisViewProps = {
  evidence?: string[] | null
  counterEvidence?: string[] | null
  invalidConditions?: string[] | null
  recoveryConditions?: string[] | null
  dataQuality?: string | null
  asOf?: string | null
  emptyText?: string
  limit?: number
}

function formatAsOf(value?: string | null) {
  if (!value) return '时点待下一次采集确认'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return `时点 ${value}`
  return `时点 ${date.toLocaleString('zh-CN', { hour12: false })}`
}

function EvidenceRows({ label, items, prefix, limit }: { label: string; items?: string[] | null; prefix: string; limit: number }) {
  return (items ?? []).filter(Boolean).slice(0, limit).map(item => (
    <p className="sensitive-evidence" key={`${label}-${item}`}>
      <b>{label}</b><span>{prefix}</span><SensitiveEvidenceText value={chineseEvidence(item)} />
    </p>
  ))
}

export default function DecisionBasisView({
  evidence,
  counterEvidence,
  invalidConditions,
  recoveryConditions,
  dataQuality,
  asOf,
  emptyText = '等待下一份真实量价、预期和执行状态快照补充依据。',
  limit = 5,
}: DecisionBasisViewProps) {
  const hasDetails = Boolean(
    evidence?.length
    || counterEvidence?.length
    || invalidConditions?.length
    || recoveryConditions?.length,
  )

  return (
    <div className="decision-basis-view">
      <EvidenceRows label="支持" prefix="+" items={evidence} limit={limit} />
      <EvidenceRows label="反证" prefix="−" items={counterEvidence} limit={limit} />
      <EvidenceRows label="失效/升级" prefix="!" items={invalidConditions} limit={Math.min(limit, 3)} />
      <EvidenceRows label="恢复/复核" prefix="↻" items={recoveryConditions} limit={Math.min(limit, 3)} />
      {!hasDetails && <p className="decision-basis-empty">{emptyText}</p>}
      <footer>
        <span>数据质量：{dataQuality ? chineseLabel(dataQuality) : '未标注'}</span>
        <time>{formatAsOf(asOf)}</time>
      </footer>
    </div>
  )
}
