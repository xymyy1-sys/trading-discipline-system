import { render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, test, expect, vi } from 'vitest'
import Dashboard, { themeScopeLabel, themeTypeLabel } from './Dashboard'

// Mock cachedJson and API_BASE
vi.mock('../api', () => ({
  API_BASE: 'http://localhost:8000',
}))

vi.mock('../apiCache', () => ({
  cachedJson: () => Promise.resolve({
    data: {
      source: 'eastmoney',
      updated_at: '2026-07-10T12:00:00Z',
      market_temperature: '轮动',
      strongest_theme: {
        name: '机器人 / 物理AI',
        board_code: 'BK0999',
        theme_type: '主线题材',
        stage: '发酵',
        stage_reason: '板块放量流入',
        score: 82,
        net_inflow: 12.5,
        main_inflow: 8.2,
        flow_ratio: 1.8,
        breadth_ratio: 0.68,
        constituent_coverage: 1,
        score_basis: ['订单流强度同类百分位90%（权重26）'],
        change_pct: 3.2,
        resonance_tags: ['主力确认', '涨停扩散'],
        related_boards: ['机器人概念', '减速器', '机器视觉'],
        core_stocks: [],
        timeline: [],
        timeline_scope: '曲线取代表板块“机器人概念”；卡片净额为全部关联板块去重成分股的当前快照，两者口径不同',
        leader_names: [],
        limit_up_count: 3,
        stock_count: 120,
        rank: 1,
        action: '等待确认',
        risk: '后排分化',
      },
      resonance: [],
      themes: [
        {
          name: '机器人 / 物理AI',
          board_code: 'BK0999',
          theme_type: '主线题材',
          stage: '发酵',
          stage_reason: '板块放量流入',
          score: 82,
          net_inflow: 12.5,
          main_inflow: 8.2,
          flow_ratio: 1.8,
          breadth_ratio: 0.68,
          constituent_coverage: 1,
          score_basis: ['订单流强度同类百分位90%（权重26）'],
          change_pct: 3.2,
          resonance_tags: ['主力确认', '涨停扩散'],
          related_boards: ['机器人概念', '减速器', '机器视觉'],
          core_stocks: [],
          timeline: [],
          timeline_scope: '曲线取代表板块“机器人概念”；卡片净额为全部关联板块去重成分股的当前快照，两者口径不同',
          leader_names: [],
          rank: 1,
          limit_up_count: 3,
          stock_count: 120,
          action: '等待确认',
          risk: '后排分化',
        }
      ],
      notes: [],
    },
    fetchedAt: '2026-07-10T12:00:00Z',
  }),
}))

describe('Dashboard Component', () => {
  test('renders theme radar title', async () => {
    const { container } = render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(container.innerHTML).toContain('机器人')
      expect(container.textContent).toContain('主线题材温度')
      expect(container.textContent).toContain('规则聚合分')
      expect(container.textContent).toContain('关联板块数')
      expect(container.textContent).toContain('规则归类板块：机器人概念 · 减速器 · 机器视觉')
      expect(container.textContent).toContain('已汇总 120 只去重成分股，展示 0 只代表股')
      expect(container.textContent).toContain('分数仅用于横向排序，不代表上涨概率或历史胜率')
      expect(container.textContent).toContain('订单流/成交额')
      expect(container.textContent).toContain('上涨家数占比')
      expect(container.textContent).toContain('查看规则聚合分依据')
      expect(container.textContent).toContain('曲线取代表板块“机器人概念”')
    })
  })

  test('labels aggregated themes without presenting them as provider categories', () => {
    expect(themeTypeLabel({ theme_type: '主线题材', related_boards: ['贵金属', '黄金概念'] })).toBe('规则聚合')
    expect(themeScopeLabel({ related_boards: ['贵金属', '黄金概念', '饰品'] })).toBe('贵金属、黄金概念等3个板块')
    expect(themeTypeLabel({ theme_type: '行业', related_boards: ['贵金属'] })).toBe('行业')
  })
})
