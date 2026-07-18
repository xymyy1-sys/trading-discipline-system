import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import StrategyTemplates from './StrategyTemplates'

vi.mock('../api', () => ({ API_BASE: 'http://localhost:8000' }))

describe('交易规则草稿真实性语义', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  test('明确草稿未接入实时决策引擎', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok([])))

    render(<StrategyTemplates />)

    expect(screen.getByRole('heading', { name: '交易规则草稿' })).toBeInTheDocument()
    expect(screen.getByText(/当前尚未接入实时决策引擎/)).toBeInTheDocument()
    expect(screen.getByText(/修改或启用不会改变系统操作建议/)).toBeInTheDocument()
    expect(screen.queryByText('交易剧本模板')).not.toBeInTheDocument()
  })

  test('保留草稿编辑和版本保存入口，但不称为实时启用', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok([template])))

    render(<StrategyTemplates />)

    expect(await screen.findByRole('checkbox', { name: '草稿标记为启用（仅用于整理）' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存草稿新版本' })).toBeInTheDocument()
    expect(screen.queryByText('启用模板')).not.toBeInTheDocument()
  })
})

const template = {
  id: 1,
  code: 'draft-1',
  name: '测试草稿',
  category: '测试',
  market_environment: [],
  prerequisites: [],
  premarket_expectation: [],
  auction_conditions: [],
  volume_price_conditions: [],
  buy_confirmation: [],
  position_limit: 0.2,
  structure_stop: [],
  invalid_conditions: [],
  holding_management: [],
  forbidden_actions: [],
  enabled: true,
  version: 1,
}

function ok(payload: unknown) {
  return { ok: true, json: async () => payload } as Response
}
