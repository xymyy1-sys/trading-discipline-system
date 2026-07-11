import '@testing-library/jest-dom'
import { vi } from 'vitest'

// Mock ResizeObserver
class MockResizeObserver {
  observe = vi.fn()
  unobserve = vi.fn()
  disconnect = vi.fn()
}
global.ResizeObserver = MockResizeObserver

// Mock ECharts canvas elements
vi.mock('echarts/core', () => {
  return {
    init: () => ({
      setOption: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      on: vi.fn(),
    }),
    use: vi.fn(),
  }
})

vi.mock('echarts/charts', () => ({
  LineChart: vi.fn(),
}))

vi.mock('echarts/components', () => ({
  GridComponent: vi.fn(),
  TooltipComponent: vi.fn(),
  LegendComponent: vi.fn(),
}))

vi.mock('echarts/renderers', () => ({
  CanvasRenderer: vi.fn(),
}))
