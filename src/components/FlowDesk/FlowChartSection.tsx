import { useEffect, useMemo, useRef } from 'react'
import type { SectorFlow, SectorFlowItem } from '../../types'
import * as echarts from 'echarts/core'

function displayName(item: SectorFlowItem) {
  return item.display_name || item.raw_name || item.name
}

export default function FlowChartSection({
  flow,
  selected,
  onSelect,
  onOpenDetail,
}: {
  flow: SectorFlow | null
  selected: string | null
  onSelect: (name: string | null) => void
  onOpenDetail: (item: SectorFlowItem) => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  const items = useMemo(() => {
    if (!flow) return []
    const top = flow.inflow.slice(0, 10)
    const bot = flow.outflow.slice(0, 10)
    return [...top, ...bot]
  }, [flow])

  const drawableItems = useMemo(() => (
    items.filter(item => item.timeline.filter(p => p.time && p.time !== '当前').length >= 2)
  ), [items])

  const xData = useMemo(() => {
    if (!drawableItems.length) return ['09:30', '15:00']
    const all = new Set<string>()
    drawableItems.forEach(it => it.timeline.forEach(p => all.add(p.time)))
    return Array.from(all).sort((a, b) => timeOrder(a) - timeOrder(b))
  }, [drawableItems])

  useEffect(() => {
    if (!ref.current || !drawableItems.length) return
    const chart = echarts.init(ref.current)
    const inflowColors = ['#d92d20', '#e23b2e', '#f05a28', '#ff6b35', '#c1121f', '#ef476f', '#ff8a3d', '#b91c1c', '#fb5607', '#e76f51']
    const outflowColors = ['#00875a', '#009a6c', '#00b894', '#2f9e44', '#2a9d8f', '#3a7d44', '#55a630', '#1b9e77', '#588157', '#4d908e']

    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 58, right: 150, top: 18, bottom: 36 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#ffffff',
        borderColor: '#d8dee6',
        textStyle: { color: '#1d2630', fontSize: 13 },
        valueFormatter: (v: unknown) => `${Number(v).toFixed(2)} 亿`,
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: xData,
        axisLine: { lineStyle: { color: '#d8dee6' } },
        axisLabel: { color: '#6b7280', fontSize: 11, rotate: xData.length > 20 ? 45 : 0 },
      },
      yAxis: {
        type: 'value',
        name: '亿元',
        nameTextStyle: { color: '#8a94a3', fontSize: 11 },
        axisLabel: { color: '#6b7280', fontSize: 11, formatter: '{value}' },
        splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } },
        axisLine: { lineStyle: { color: '#d8dee6' } },
      },
      series: drawableItems.map((item, i) => {
        const isInflow = item.net_inflow >= 0
        const colorIdx = isInflow ? i % inflowColors.length : i % outflowColors.length
        const color = isInflow ? inflowColors[colorIdx] : outflowColors[colorIdx]
        const label = displayName(item)
        const isSelected = selected === label
        return {
          name: label,
          type: 'line',
          smooth: 0.4,
          symbol: 'none',
          endLabel: {
            show: true,
            formatter: () => `#${item.rank} ${label} ${item.net_inflow >= 0 ? '+' : ''}${item.net_inflow.toFixed(1)}亿`,
            color,
            fontSize: 11,
            fontWeight: 600,
          },
          labelLayout: { moveOverlap: 'shiftY' },
          emphasis: { focus: 'series' },
          lineStyle: {
            width: isSelected ? 4 : Math.abs(item.net_inflow) > 8 ? 2.4 : 1.6,
            color,
            opacity: selected && !isSelected ? 0.25 : 0.9,
          },
          data: xData.map(t => {
            const pt = item.timeline.find(p => p.time === t)
            return pt ? pt.value : null
          }),
          connectNulls: true,
          markPoint: isSelected && item.timeline_reliable && item.flow_peak_time ? {
            symbolSize: 42,
            label: {
              formatter: `峰值\n${item.flow_peak?.toFixed(1)}亿`,
              color: '#fff',
              fontSize: 10,
            },
            data: [{ coord: [item.flow_peak_time, item.flow_peak] }],
          } : undefined,
        }
      }),
      color: [...inflowColors, ...outflowColors],
    })

    chart.on('click', (params: any) => {
      if (params.seriesName) {
        onSelect(params.seriesName === selected ? null : params.seriesName)
        const item = drawableItems.find(it => displayName(it) === params.seriesName)
        if (item) onOpenDetail(item)
      }
    })

    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [drawableItems, xData, selected, onSelect, onOpenDetail])

  if (!items.length) {
    return <div className="chart-surface-large chart-empty-state">暂无资金流数据</div>
  }

  if (!drawableItems.length) {
    return (
      <div className="chart-surface-large chart-empty-state">
        <strong>当前只有快照数据</strong>
        <span>主图不再绘制孤立点；请看右侧 TOP10 榜单和资金拆解，盘中多次刷新后自动形成连续曲线。</span>
      </div>
    )
  }

  return <div className="chart-surface-large" ref={ref} aria-label="主力行业资金流入流出 TOP10 曲线" />
}

function timeOrder(label: string) {
  if (label === '当前') return 2400
  const match = label.match(/^(\d{1,2}):(\d{2})$/)
  if (!match) return Number.MAX_SAFE_INTEGER
  return Number(match[1]) * 60 + Number(match[2])
}
