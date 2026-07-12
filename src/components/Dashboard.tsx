import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { ThemeRadarItem, ThemeRadar } from '../types'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Flame,
  Layers3,
  RadioTower,
  RefreshCcw,
  Target,
  TrendingUp,
  UsersRound,
} from 'lucide-react'
import { API_BASE } from '../api'
import { cachedJson } from '../apiCache'

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
])

export default function Dashboard() {
  const [radar, setRadar] = useState<ThemeRadar | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [fetchedAt, setFetchedAt] = useState<string | null>(null)

  const loadRadar = (force = false) => {
    setLoading(true)
    const forceParam = force ? '?force_refresh=true' : ''
    cachedJson<ThemeRadar>('theme-radar', `${API_BASE}/api/market/theme-radar${forceParam}`, force)
      .then(({ data, fetchedAt }) => {
        setRadar(data)
        setSelectedName(data.strongest_theme?.name ?? data.themes[0]?.name ?? null)
        setFetchedAt(fetchedAt)
        setError('')
      })
      .catch(() => setError('题材雷达暂不可用，请确认后端服务和行情源'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadRadar()
    const timer = setInterval(() => loadRadar(), 300000)
    return () => clearInterval(timer)
  }, [])

  const selected = useMemo(() => {
    if (!radar?.themes.length) return null
    return radar.themes.find(item => item.name === selectedName) ?? radar.strongest_theme ?? radar.themes[0]
  }, [radar, selectedName])

  const chartThemes = useMemo(() => {
    if (!radar?.themes.length) return []
    const names = new Set<string>()
    const items: ThemeRadarItem[] = []
    ;[...(radar.resonance ?? []), ...(radar.themes ?? [])].forEach(item => {
      if (items.length >= 8 || names.has(item.name) || !item.timeline?.length) return
      names.add(item.name)
      items.push(item)
    })
    return items
  }, [radar])

  const sourceLabel = radar?.source.includes('diagnostic')
    ? '诊断数据'
    : radar?.source.includes('sina')
      ? '新浪资金流'
    : radar?.source.includes('eastmoney')
      ? '东方财富'
      : '同步中'

  const topThemes = radar?.themes.slice(0, 12) ?? []

  return (
    <div className="theme-radar trading-desk-page">
      <section className="radar-command">
        <div className="desk-heading">
          <span className="eyebrow">主线题材雷达</span>
          <h2>{radar?.strongest_theme?.name ?? '题材雷达'}</h2>
          <p>
            {radar?.strongest_theme
              ? `${radar.strongest_theme.stage} · ${radar.strongest_theme.stage_reason}`
              : loading ? '正在同步板块资金、涨幅和核心股表现' : error || '暂无有效题材数据'}
          </p>
        </div>
        <div className="radar-command-actions">
          <button className="refresh-btn" type="button" onClick={() => loadRadar(true)} disabled={loading}>
            <RefreshCcw size={15} />
            {loading ? '同步中' : '刷新'}
          </button>
          <div className="desk-source">
            <span>{sourceLabel}</span>
            <strong>{radar ? new Date(radar.updated_at).toLocaleTimeString('zh-CN') : '--'}</strong>
            <small>{fetchedAt ? `缓存 ${new Date(fetchedAt).toLocaleTimeString('zh-CN')}` : '5 分钟缓存'}</small>
          </div>
        </div>
      </section>

      <section className="radar-pulse-strip">
        <Signal label="市场温度" value={radar?.market_temperature ?? '--'} icon={<Flame size={16} />} />
        <Signal label="最强题材分" value={radar?.strongest_theme ? `${radar.strongest_theme.score}` : '--'} icon={<Activity size={16} />} />
        <Signal label="题材净流入" value={radar?.strongest_theme ? `${fmtSigned(radar.strongest_theme.net_inflow)}亿` : '--'} icon={<TrendingUp size={16} />} tone="up" />
        <Signal label="共振数量" value={`${radar?.resonance.length ?? 0}`} icon={<Layers3 size={16} />} />
        <Signal label="阶段" value={radar?.strongest_theme?.stage ?? '--'} icon={<RadioTower size={16} />} />
      </section>

      <section className="radar-workbench">
        <article className="panel theme-ranking-panel">
          <div className="panel-title-line">
            <h3><BarChart3 size={16} /> 主线强度排行</h3>
            <span>{topThemes.length ? `前 ${topThemes.length} 名` : '等待数据'}</span>
          </div>
          <div className="theme-rank-list">
            {topThemes.map(item => (
              <button
                className={`theme-rank-row ${selected?.name === item.name ? 'active' : ''}`}
                key={`${item.theme_type}-${item.name}`}
                onClick={() => setSelectedName(item.name)}
                type="button"
              >
                <span className="theme-rank-num">{String(item.rank).padStart(2, '0')}</span>
                <span className="theme-rank-main">
                  <strong>{item.name}</strong>
                  <small>{item.theme_type} / {item.stage} / {(item.related_boards ?? []).slice(0, 2).join('、') || '无关联板块'}</small>
                </span>
                <span className="theme-score">{item.score}</span>
                <span className="theme-rank-bar"><i style={{ width: `${Math.max(5, item.score)}%` }} /></span>
              </button>
            ))}
            {!loading && !topThemes.length && <p className="empty-msg">暂无题材数据。</p>}
          </div>
        </article>

        <article className="panel selected-theme-panel">
          <div className="panel-title-line">
            <h3><Target size={16} /> 选中题材证据</h3>
            <span>{selected?.stage ?? '--'}</span>
          </div>
          {selected ? (
            <>
              <div className="selected-theme-head">
                <div>
                  <strong>{selected.name}</strong>
                  <span>{selected.resonance_tags.join(' / ') || '等待共振标签'}</span>
                  {!!selected.related_boards?.length && <em>{selected.related_boards.slice(0, 6).join(' · ')}</em>}
                </div>
                <b>{selected.theme_type}</b>
              </div>
              <div className="theme-stat-grid">
                <MiniStat label="涨幅" value={`${fmtSigned(selected.change_pct)}%`} tone={selected.change_pct >= 0 ? 'up' : 'down'} />
                <MiniStat label="净流入" value={`${fmtSigned(selected.net_inflow)}亿`} tone={selected.net_inflow >= 0 ? 'up' : 'down'} />
                <MiniStat label="主力流入" value={`${fmtSigned(selected.main_inflow)}亿`} tone={selected.main_inflow >= 0 ? 'up' : 'down'} />
                <MiniStat label="涨停扩散" value={`${selected.limit_up_count || '--'}只`} />
              </div>
              <div className="stock-role-list">
                {selected.core_stocks.length ? selected.core_stocks.map(stock => (
                  <div className="stock-role-row" key={`${stock.role}-${stock.code}-${stock.name}`}>
                    <span className="role-badge">{stock.role}</span>
                    <strong>{stock.name}</strong>
                    <span className={stock.change_pct >= 0 ? 'num-up' : 'num-down'}>{fmtSigned(stock.change_pct)}%</span>
                    <small>{stock.amount ? `${stock.amount.toFixed(2)}亿 · ` : ''}{stock.reason}</small>
                  </div>
                )) : <p className="plain-text">暂无核心股证据，降低题材确认等级。</p>}
              </div>
            </>
          ) : (
            <p className="plain-text">等待选择题材。</p>
          )}
        </article>

        <article className="panel theme-flow-panel">
          <div className="panel-title-line">
            <h3><TrendingUp size={16} /> 资金曲线</h3>
            <span>{chartThemes.length} 条曲线</span>
          </div>
          <ThemeFlowChart items={chartThemes} selectedName={selected?.name ?? null} />
        </article>
      </section>

      <section className="radar-bottom-grid">
        <article className="panel">
          <h3><UsersRound size={16} /> 龙头线索</h3>
          <div className="watchlist-tags">
            {(selected?.leader_names?.length ? selected.leader_names : ['等待核心股确认']).map(name => <span key={name}>{name}</span>)}
          </div>
          <p className="plain-text">{selected?.action ?? '只在题材、资金、核心股三者同向时提高关注。'}</p>
        </article>
        <article className="panel">
          <h3><AlertTriangle size={16} /> 阶段风险</h3>
          <p className="plain-text">{selected?.risk ?? '等待题材阶段确认。'}</p>
          <div className="rule-list">
            <span>资金流只是证据，不单独作为买点</span>
            <span>后排跟风必须让位于情绪龙头和容量中军</span>
            <span>高潮阶段先看兑现风险，再看进攻机会</span>
          </div>
        </article>
        <article className="panel resonance-panel">
          <h3><Layers3 size={16} /> 共振题材</h3>
          <div className="compact-chip-list">
            {(radar?.resonance ?? []).slice(0, 8).map(item => (
              <button key={item.name} type="button" onClick={() => setSelectedName(item.name)}>
                <strong>{item.name}</strong>
                <span>{item.score}</span>
              </button>
            ))}
            {!radar?.resonance.length && <span>暂无三因子以上共振</span>}
          </div>
        </article>
      </section>

      <section className="panel radar-notes">
        <h3><RadioTower size={16} /> 数据备注</h3>
        <ul className="reason-list">
          {(radar?.notes ?? [error || '等待同步']).slice(0, 5).map((note, i) => <li key={i}>{note}</li>)}
        </ul>
      </section>
    </div>
  )
}

function ThemeFlowChart({ items, selectedName }: { items: ThemeRadarItem[]; selectedName: string | null }) {
  const ref = useRef<HTMLDivElement>(null)

  const xData = useMemo(() => {
    if (!items.length) return []
    const all = new Set<string>()
    items.forEach(item => item.timeline.forEach(point => all.add(point.time)))
    return Array.from(all).sort()
  }, [items])

  useEffect(() => {
    if (!ref.current || !items.length || !xData.length) return
    const chart = echarts.init(ref.current)
    const colors = ['#c92a2a', '#16834f', '#9a6700', '#285f9f', '#5f4b8b', '#0b7285', '#b45500', '#596574']
    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 48, right: 18, top: 16, bottom: 34 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#ffffff',
        borderColor: '#d8dee6',
        textStyle: { color: '#1d2630', fontSize: 12 },
        valueFormatter: (v: unknown) => `${Number(v).toFixed(2)} 亿`,
      },
      legend: {
        type: 'scroll',
        bottom: 0,
        left: 'center',
        itemWidth: 10,
        itemHeight: 7,
        textStyle: { color: '#596574', fontSize: 11 },
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: xData,
        axisLine: { lineStyle: { color: '#d8dee6' } },
        axisLabel: {
          color: '#6b7280',
          fontSize: 10,
          interval: Math.max(0, Math.floor(xData.length / 8) - 1),
        },
      },
      yAxis: {
        type: 'value',
        name: '亿元',
        nameTextStyle: { color: '#8a94a3', fontSize: 10 },
        axisLabel: { color: '#6b7280', fontSize: 10 },
        splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } },
      },
      series: items.map((item, i) => {
        const isSelected = selectedName === item.name
        const color = colors[i % colors.length]
        return {
          name: item.name,
          type: 'line',
          smooth: 0.35,
          symbol: 'none',
          connectNulls: true,
          emphasis: { focus: 'series' },
          lineStyle: {
            width: isSelected ? 3.5 : 1.8,
            color,
            opacity: selectedName && !isSelected ? 0.25 : 0.95,
          },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(201,42,42,0.10)' },
                { offset: 1, color: 'rgba(255,255,255,0)' },
              ],
            },
            opacity: isSelected ? 0.16 : 0.03,
          },
          data: xData.map(time => item.timeline.find(point => point.time === time)?.value ?? null),
        }
      }),
      color: colors,
    })
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [items, selectedName, xData])

  if (!items.length) {
    return <div className="theme-flow-empty">等待板块资金曲线</div>
  }
  return <div className="theme-flow-chart" ref={ref} aria-label="板块资金曲线" />
}

function Signal({ label, value, icon, tone }: { label: string; value: string; icon: ReactNode; tone?: 'up' }) {
  return (
    <div className={`signal ${tone ?? ''}`}>
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function MiniStat({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  )
}

function fmtSigned(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`
}
