import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import ReviewCalibration from './ReviewCalibration'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

const response = (payload: unknown) => ({ ok: true, json: async () => payload, text: async () => '' } as Response)

describe('复盘校准可靠样本闸门', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('可靠结果样本不足时只显示闭环诊断，不展示模型有效性和自动参数建议', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/review-calibration/summary')) return response({
        trade_count: 8, review_count: 3, plan_review_count: 2, missing_plan_review_count: 6,
        execution_feedback_count: 2, ignored_recommendation_count: 0, pending_review_count: 5,
        avg_discipline_score: 70, focus: '先补齐建议—执行—结果闭环', issues: [], recent_plan_deviations: [],
        feedback_summary: [], model_metrics: [{ key: 'x', label: '不应显示', sample_count: 2, success_count: 2, fail_count: 0, success_rate: 100, average_value: 1, verdict: '有效', evidence: [] }],
        calibration_suggestions: [{ level: '高', target: '不应显示', suggestion: '自动改参数', reason: '小样本', sample_count: 2 }],
        next_actions: ['补执行反馈'],
      })
      if (url.endsWith('/api/reviews/calibration-proposal')) return response({ metric_key: 'expectation', sample_count: 2, minimum_samples: 20, eligible: false, rationale: '完整结果样本不足', changes: [] })
      if (url.endsWith('/api/reviews/recommendation-outcomes/summary')) return response({ price_outcome_sample_count: 42, calibration_eligible_sample_count: 2, minimum_calibration_samples: 20 })
      if (url.endsWith('/api/data-quality/health')) return response({ providers: [] })
      if (url.endsWith('/api/reviews/environment-effectiveness')) return response([])
      throw new Error(`unexpected request: ${url}`)
    }))

    render(<ReviewCalibration />)

    expect(await screen.findByRole('heading', { name: '结果闭环诊断' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '模型有效性' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /参数建议/ })).not.toBeInTheDocument()
    expect(screen.getByText('完整结果样本不足')).toBeInTheDocument()
  })
})
