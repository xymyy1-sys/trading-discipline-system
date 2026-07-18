import type { EffectiveCapitalEvidence as EffectiveCapitalEvidenceData } from '../types'

type Props = {
  evidence?: EffectiveCapitalEvidenceData | null
}

type Tone = 'positive' | 'support' | 'danger' | 'waiting' | 'insufficient'

export default function EffectiveCapitalEvidence({ evidence }: Props) {
  if (!evidence) {
    return (
      <section className="effective-capital-panel insufficient" aria-label="订单流有效性证据链">
        <EvidenceHeader
          label="证据不足，暂不判断"
          tone="insufficient"
          confidence={null}
          quality="接口尚未返回有效订单流证据"
        />
        <p className="effective-capital-empty">没有可核验的主动成交方向、价格响应和持续性数据，因此不生成“资金介入”结论。</p>
        <EvidenceBoundary />
      </section>
    )
  }

  const tone = capitalTone(evidence.state, evidence.state_label)
  const metrics = evidence.metrics
  const hasMetrics = metrics && [
    metrics.active_buy_yi,
    metrics.active_sell_yi,
    metrics.signed_flow_yi,
    metrics.buy_ratio,
    metrics.active_flow_coverage_ratio,
    metrics.price_change_pct,
    metrics.vwap_distance_pct,
    metrics.price_response_per_signed_yi,
    metrics.impact_retention_pct,
    metrics.persistence_score,
  ].some(value => typeof value === 'number' && Number.isFinite(value))
  const insufficient = tone === 'insufficient' || (!hasMetrics && evidence.evidence.length === 0)
  const confidence = normalizedPercent(evidence.confidence)

  return (
    <section className={`effective-capital-panel ${insufficient ? 'insufficient' : tone}`} aria-label="订单流有效性证据链">
      <EvidenceHeader
        label={insufficient ? '证据不足，暂不判断' : evidence.state_label || stateLabel(evidence.state)}
        tone={insufficient ? 'insufficient' : tone}
        confidence={confidence}
        quality={qualityLabel(evidence.data_quality)}
      />

      {hasMetrics && metrics && (
        <div className="effective-capital-metrics">
          <Metric label="主动买入估算" value={formatYi(metrics.active_buy_yi)} />
          <Metric label="主动卖出估算" value={formatYi(metrics.active_sell_yi)} />
          <Metric label="方向差额估算" value={formatYi(metrics.signed_flow_yi, true)} tone={numberTone(metrics.signed_flow_yi)} />
          <Metric label="主动买入占比" value={formatFractionPercent(metrics.buy_ratio)} />
          <Metric label="方向分类覆盖率" value={formatFractionPercent(metrics.active_flow_coverage_ratio)} />
          <Metric label="价格变化" value={formatPercent(metrics.price_change_pct, true)} tone={numberTone(metrics.price_change_pct)} />
          <Metric label="偏离分时均价" value={formatPercent(metrics.vwap_distance_pct, true)} tone={numberTone(metrics.vwap_distance_pct)} />
          <Metric label="单位方向差额价格响应" value={formatEfficiency(metrics.price_response_per_signed_yi)} />
          <Metric label="同刻历史规模分位" value={formatFlowPercentile(metrics.same_time_flow_percentile, metrics.normalization_sample_count)} />
          <Metric label="冲击保持率" value={formatFractionPercent(metrics.impact_retention_pct)} />
          <Metric label="持续性评分" value={formatScore(metrics.persistence_score)} />
          <Metric label="观察窗口" value={formatWindow(metrics.window_minutes, metrics.sample_count)} />
        </div>
      )}

      {insufficient
        ? <>
            <p className="effective-capital-empty">当前数据无法同时验证订单流方向、价格响应和持续时间，不以孤立成交额推断大资金介入。</p>
            <div className="effective-capital-columns diagnostic">
              <EvidenceList title="未形成结论的原因" items={evidence.evidence} empty="接口没有返回可复核的失败原因。" />
            </div>
          </>
        : <div className="effective-capital-columns">
            <EvidenceList title="支持与反向证据" items={evidence.evidence} empty="暂无可复核证据明细。" />
            <EvidenceList title="结论失效条件" items={evidence.invalidation} empty="失效条件尚未形成，当前结论不可用于下单。" />
            <EvidenceList title="操作纪律" items={evidence.discipline} empty="仅作观察，不自动形成买卖动作。" />
          </div>}

      {!!evidence.warnings.length && (
        <div className="effective-capital-warnings" role="note">
          <b>数据边界</b>
          {evidence.warnings.map(item => <span key={item}>• {item}</span>)}
        </div>
      )}

      <footer className="effective-capital-meta">
        <span>口径：{insufficient ? '可用数据源待确认' : evidence.source_label || '数据源待确认'}</span>
        <span>截至：{formatAsOf(evidence.as_of)}</span>
        <strong>{insufficient ? '未形成可用方向分类' : evidence.estimated ? '分钟方向估算' : '供应商方向分类'}</strong>
      </footer>
      <EvidenceBoundary />
    </section>
  )
}

function EvidenceHeader({ label, tone, confidence, quality }: { label: string; tone: Tone; confidence: number | null; quality: string }) {
  return (
    <header className="effective-capital-head">
      <div>
        <small>订单流方向 × 价格响应 × 持续性</small>
        <h3>订单流有效性证据链</h3>
        <p>当前状态：<strong className={`capital-state ${tone}`}>{label}</strong></p>
      </div>
      <div className="effective-capital-score">
        <span>证据置信度</span>
        <b>{confidence === null ? '--' : `${confidence.toFixed(0)}%`}</b>
        <small>{quality}</small>
      </div>
    </header>
  )
}

function EvidenceBoundary() {
  return (
    <p className="effective-capital-boundary">
      本模块验证成交方向与价格响应是否同步，无法识别交易账户身份，也不代表机构或所谓“主力”的真实资金流水；不得单独作为买卖理由。
    </p>
  )
}

function EvidenceList({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div>
      <b>{title}</b>
      {items.length
        ? items.slice(0, 5).map(item => <p key={item}>• {item}</p>)
        : <p className="effective-capital-empty-copy">{empty}</p>}
    </div>
  )
}

function Metric({ label, value, tone = '' }: { label: string; value: string; tone?: '' | 'up' | 'down' }) {
  return <span><b>{label}</b><strong className={tone ? `num-${tone}` : ''}>{value}</strong></span>
}

function capitalTone(state: string, label: string): Tone {
  const value = `${state || ''} ${label || ''}`.toUpperCase()
  if (/INSUFFICIENT|UNAVAILABLE|UNKNOWN|数据不足|不可用|无法判断/.test(value)) return 'insufficient'
  if (/DISTRIBUT|OUTFLOW|LIQUIDITY_SHOCK|SELL|WEAK|RISK|派发|流出有效|流动性冲击|兑现|风险/.test(value)) return 'danger'
  if (/ABSORB|RECOVERY|SUPPORT|承接|修复候选|吸收|拒绝新低/.test(value)) return 'support'
  if (/ATTACK|INFLOW_EFFECTIVE|进攻有效|流入有效/.test(value)) return 'positive'
  return 'waiting'
}

function stateLabel(state: string) {
  const labels: Record<string, string> = {
    EFFECTIVE_ATTACK: '进攻有效',
    ATTACK_EFFECTIVE: '进攻有效',
    ATTACK_CONFIRMED: '买向成交与上涨同步',
    ABSORPTION: '下方承接',
    ABSORPTION_CANDIDATE: '下方承接候选',
    RECOVERY_CANDIDATE: '深水修复候选',
    DISTRIBUTION: '买盘推动不足',
    DISTRIBUTION_RISK: '买盘推动不足风险',
    EFFECTIVE_OUTFLOW: '流出有效',
    OUTFLOW_EFFECTIVE: '流出有效',
    OUTFLOW_CONFIRMED: '卖向成交与下跌同步',
    LIQUIDITY_SHOCK: '流动性冲击',
    UNRESOLVED: '方向未决',
    INCONCLUSIVE: '方向未决',
    INSUFFICIENT_DATA: '证据不足，暂不判断',
  }
  return labels[(state || '').toUpperCase()] || '方向未决'
}

function qualityLabel(value: string) {
  const labels: Record<string, string> = {
    realtime: '实时证据',
    good: '数据质量良好',
    partial: '部分证据可用',
    stale: '数据已过期',
    insufficient: '数据不足',
    unavailable: '数据不可用',
    untrusted: '数据未通过校验',
    missing: '数据缺失',
    historical_close: '上一交易日收盘证据',
  }
  return labels[(value || '').toLowerCase()] || value || '数据质量待确认'
}

function normalizedPercent(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return value >= 0 && value <= 1 ? value * 100 : value
}

function formatYi(value: number | null | undefined, signed = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${signed && value > 0 ? '+' : ''}${value.toFixed(2)}亿`
}

function formatPercent(value: number | null | undefined, signed = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  const normalized = !signed && value >= 0 && value <= 1 ? value * 100 : value
  return `${signed && normalized > 0 ? '+' : ''}${normalized.toFixed(2)}%`
}

function formatFractionPercent(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${(value * 100).toFixed(2)}%`
}

function formatEfficiency(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${value.toFixed(3)}点/亿`
}

function formatFlowPercentile(value: number | null | undefined, sampleCount: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return `--（${sampleCount ?? 0}样本）`
  return `${value.toFixed(1)}%（${sampleCount ?? 0}样本）`
}

function formatScore(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${(value * 100).toFixed(0)}/100`
}

function formatWindow(value: number | null | undefined, sampleCount: number | null | undefined) {
  const samples = typeof sampleCount === 'number' && Number.isFinite(sampleCount)
    ? Math.max(0, Math.round(sampleCount))
    : 0
  const span = typeof value === 'number' && Number.isFinite(value)
    ? Math.max(0, Math.round(value))
    : null
  if (!samples && span === null) return '--'
  if (!samples) return `跨${span}分钟`
  if (span === null) return `${samples}个分钟样本`
  return `${samples}个分钟样本 / 跨${span}分钟`
}

function formatAsOf(value: string | null | undefined) {
  if (!value) return '待确认'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function numberTone(value: number | null | undefined): '' | 'up' | 'down' {
  if (typeof value !== 'number' || !Number.isFinite(value) || value === 0) return ''
  return value > 0 ? 'up' : 'down'
}
