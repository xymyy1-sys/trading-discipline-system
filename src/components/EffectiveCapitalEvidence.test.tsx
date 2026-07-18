import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test } from 'vitest'
import type { EffectiveCapitalEvidence as EffectiveCapitalEvidenceData } from '../types'
import EffectiveCapitalEvidence from './EffectiveCapitalEvidence'

const base: EffectiveCapitalEvidenceData = {
  state: 'EFFECTIVE_ATTACK',
  state_label: '买向成交与上涨同步',
  confidence: 0.82,
  state_severity: 'POSITIVE',
  data_quality: 'realtime',
  source_label: '逐笔成交方向估算 + 分钟量价',
  as_of: '2026-07-18T10:32:00+08:00',
  estimated: true,
  metrics: {
    sample_count: 10,
    active_buy_yi: 2.5,
    active_sell_yi: 1.1,
    signed_flow_yi: 1.4,
    buy_ratio: 0.69,
    active_flow_coverage_ratio: 0.72,
    same_time_flow_percentile: 83.3,
    normalization_sample_count: 30,
    price_change_pct: 2.3,
    vwap_distance_pct: 0.8,
    price_response_per_signed_yi: 0.24,
    impact_retention_pct: 0.76,
    persistence_score: 0.84,
    window_minutes: 15,
  },
  evidence: ['主动买入持续占优，价格同步抬升。'],
  warnings: ['主动成交方向为算法估算。'],
  invalidation: ['回落跌破分时均价且资金转负。'],
  discipline: ['等待回踩确认，不追直线。'],
  reason_codes: [],
}

describe('订单流有效性证据链', () => {
  afterEach(cleanup)

  test('展示估算口径、价格响应和结论失效条件', () => {
    const { container } = render(<EffectiveCapitalEvidence evidence={base} />)

    expect(screen.getByRole('region', { name: '订单流有效性证据链' })).toBeInTheDocument()
    expect(screen.getByText('买向成交与上涨同步')).toBeInTheDocument()
    expect(screen.getByText('82%')).toBeInTheDocument()
    expect(screen.getByText('+1.40亿')).toBeInTheDocument()
    expect(screen.getByText('76.00%')).toBeInTheDocument()
    expect(screen.getByText('84/100')).toBeInTheDocument()
    expect(screen.getByText('10个分钟样本 / 跨15分钟')).toBeInTheDocument()
    expect(screen.getByText('分钟方向估算')).toBeInTheDocument()
    expect(screen.getByText(/无法识别交易账户身份/)).toBeInTheDocument()
    expect(screen.getByText(/回落跌破分时均价/)).toBeInTheDocument()
    expect(container.querySelector('.effective-capital-panel')).toHaveClass('positive')
  })

  test('流出和买盘推动不足使用高风险红色语义', () => {
    const { container } = render(<EffectiveCapitalEvidence evidence={{ ...base, state: 'DISTRIBUTION', state_label: '买盘推动不足' }} />)
    expect(screen.getByText('买盘推动不足')).toHaveClass('danger')
    expect(container.querySelector('.effective-capital-panel')).toHaveClass('danger')
  })

  test('深水V形修复使用观察语义并同时提示不恐慌追卖和不追高补仓', () => {
    const recovery: EffectiveCapitalEvidenceData = {
      ...base,
      state: 'RECOVERY_CANDIDATE',
      state_label: '深水修复候选',
      state_severity: 'WATCH',
      metrics: {
        ...base.metrics!,
        price_change_pct: 5.26,
        vwap_distance_pct: -4.76,
        impact_retention_pct: 1,
      },
      discipline: [
        '避免在窗口低点附近恐慌卖出；但尚未收回真实分时均价，禁止追高或逆势补仓。',
        '等待放量站回真实分时均价并维持至少三个分钟。',
      ],
    }

    const { container } = render(<EffectiveCapitalEvidence evidence={recovery} />)

    expect(screen.getByText('深水修复候选')).toHaveClass('support')
    expect(container.querySelector('.effective-capital-panel')).toHaveClass('support')
    expect(screen.getByText(/避免在窗口低点附近恐慌卖出/)).toBeInTheDocument()
    expect(screen.getByText(/禁止追高或逆势补仓/)).toBeInTheDocument()
    expect(container.querySelector('.effective-capital-panel')).not.toHaveClass('danger')
  })

  test('后端尚未返回时诚实显示证据不足', () => {
    render(<EffectiveCapitalEvidence evidence={null} />)
    expect(screen.getByText('证据不足，暂不判断')).toBeInTheDocument()
    expect(screen.getByText(/不生成“资金介入”结论/)).toBeInTheDocument()
    expect(screen.queryByText('买向成交与上涨同步')).not.toBeInTheDocument()
  })

  test('超过100%的覆盖率和保持率仍按比例显示', () => {
    render(<EffectiveCapitalEvidence evidence={{
      ...base,
      metrics: { ...base.metrics!, active_flow_coverage_ratio: 1.1, impact_retention_pct: 1.2 },
    }} />)
    expect(screen.getByText('110.00%')).toBeInTheDocument()
    expect(screen.getByText('120.00%')).toBeInTheDocument()
  })

  test('无指标且无证据时明确显示未形成可用方向分类', () => {
    render(<EffectiveCapitalEvidence evidence={{
      ...base,
      state: 'INSUFFICIENT_DATA',
      state_label: '证据不足，暂不判断',
      confidence: 0,
      data_quality: 'insufficient',
      metrics: null,
      evidence: [],
      warnings: [],
      invalidation: [],
      discipline: [],
    }} />)

    expect(screen.getByText('未形成可用方向分类')).toBeInTheDocument()
    expect(screen.queryByText('供应商方向分类')).not.toBeInTheDocument()
  })
})
