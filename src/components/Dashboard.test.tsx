import { render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, test, expect, vi } from 'vitest'
import Dashboard from './Dashboard'

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
        stage: '发酵',
        stage_reason: '板块放量流入',
        score: 82,
        net_inflow: 12.5,
        main_inflow: 8.2,
        change_pct: 3.2,
        resonance_tags: ['主力确认', '涨停扩散'],
        related_boards: [],
        core_stocks: [],
        timeline: [],
        leader_names: [],
      },
      resonance: [],
      themes: [
        {
          name: '机器人 / 物理AI',
          stage: '发酵',
          stage_reason: '板块放量流入',
          score: 82,
          net_inflow: 12.5,
          main_inflow: 8.2,
          change_pct: 3.2,
          resonance_tags: ['主力确认', '涨停扩散'],
          related_boards: [],
          core_stocks: [],
          timeline: [],
          leader_names: [],
          rank: 1,
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
    })
  })
})
