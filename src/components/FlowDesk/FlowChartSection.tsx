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
    const top = flow.inflow.slice(0, 14)
    const bot = flow.outflow.slice(0, 18)
    return [...top, ...bot]
  }, [flow])

  const xData = useMemo(() => {
    if (!items.length) return ['09:30', '15:00']
    const all = new Set<string>()
    items.forEach(it => it.timeline.forEach(p => all.add(p.time)))
    return Array.from(all).sort()
  }, [items])

  useEffect(() => {
    if (!ref.current || !items.length) return
    const chart = echarts.init(ref.current)
    const inflowColors = ['#d92d20', '#e23b2e', '#f05a28', '#ff6b35', '#d62828', '#f77f00', '#c1121f', '#ef476f', '#ff8a3d', '#b91c1c', '#fb5607', '#d9480f', '#f94144', '#e76f51']
    const outflowColors = ['#00875a', '#009a6c', '#00b894', '#2f9e44', '#43aa8b', '#2a9d8f', '#3a7d44', '#55a630', '#1b9e77', '#66a182', '#6abf69', '#588157', '#4d908e', '#52b788', '#40916c', '#6c757d', '#adb5bd', '#8d99ae']

    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 56, right: 20, top: 12, bottom: 40 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#ffffff',
        borderColor: '#d8dee6',
        textStyle: { color: '#1d2630', fontSize: 13 },
        valueFormatter: (v: unknown) => `${Number(v).toFixed(2)} 亿`,
      },
      legend: {
        type: 'scroll',
        bottom: 6,
        left: 'center',
        textStyle: { color: '#596574', fontSize: 11 },
        pageTextStyle: { color: '#596574' },
        itemWidth: 12,
        itemHeight: 8,
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
      },
      series: items.map((item, i) => {
        const isInflow = item.net_inflow >= 0
        const colorIdx = isInflow ? i % inflowColors.length : (i - flow!.inflow.slice(0, 14).length) % outflowColors.length
        const color = isInflow ? inflowColors[colorIdx] : outflowColors[colorIdx]
        const label = displayName(item)
        const isSelected = selected === label
        return {
          name: label,
          type: 'line',
          smooth: 0.4,
          symbol: 'none',
          emphasis: { focus: 'series' },
          lineStyle: {
            width: isSelected ? 4 : Math.abs(item.net_inflow) > 8 ? 2.5 : 1.5,
            color,
            opacity: selected && !isSelected ? 0.25 : 0.9,
          },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: isInflow ? 'rgba(217,45,32,0.10)' : 'rgba(0,135,90,0.08)' },
                { offset: 1, color: 'rgba(255,255,255,0)' },
              ],
            },
            opacity: isSelected ? 0.2 : 0.05,
          },
          data: xData.map(t => {
            const pt = item.timeline.find(p => p.time === t)
            return pt ? pt.value : null
          }),
          connectNulls: true,
        }
      }),
      color: [...inflowColors, ...outflowColors],
    })

    chart.on('click', (params: any) => {
      if (params.seriesName) {
        onSelect(params.seriesName === selected ? null : params.seriesName)
        const item = items.find(it => displayName(it) === params.seriesName)
        if (item) onOpenDetail(item)
      }
    })

    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [items, xData, selected, onSelect, onOpenDetail, flow])

  return <div className="chart-surface-large" ref={ref} aria-label="全天资金流向图" />
}
