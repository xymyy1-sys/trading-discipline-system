import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as echarts from 'echarts'
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

type ThemeStockRole = {
  code: string
  name: string
  role: string
  change_pct: number
  amount: number
  reason: string
}

type FlowPoint = { time: string; value: number }

type ThemeRadarItem = {
  name: string
  board_code: string | null
  theme_type: string
  related_boards: string[]
  stage: string
  stage_reason: string
  score: number
  rank: number
  change_pct: number
  net_inflow: number
  main_inflow: number
  limit_up_count: number
  stock_count: number
  leader_names: string[]
  core_stocks: ThemeStockRole[]
  timeline: FlowPoint[]
  resonance_tags: string[]
  action: string
  risk: string
}

type ThemeRadar = {
  source: string
  updated_at: string
  market_temperature: string
  strongest_theme: ThemeRadarItem | null
  resonance: ThemeRadarItem[]
  themes: ThemeRadarItem[]
  notes: string[]
}

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

  return (
    <div className="theme-radar">
      <section className="radar-hero">
        <div className="radar-hero-main">
          <span className="eyebrow">Theme Radar</span>
          <h2>{radar?.strongest_theme?.name ?? '等待题材信号'}</h2>
          <p>
            {radar?.strongest_theme
              ? `${radar.strongest_theme.stage} · ${radar.strongest_theme.stage_reason}`
              : loading ? '正在同步板块资金、涨幅和核心股表现' : error || '暂无有效题材数据'}
          </p>
          <div className="hero-signal-row">
            <Signal label="市场温度" value={radar?.market_temperature ?? '--'} icon={<Flame size={16} />} />
            <Signal label="题材强度" value={radar?.strongest_theme ? `${radar.strongest_theme.score}` : '--'} icon={<Activity size={16} />} />
            <Signal label="净流入" value={radar?.strongest_theme ? `${fmtSigned(radar.strongest_theme.net_inflow)}亿` : '--'} icon={<TrendingUp size={16} />} tone="up" />
            <Signal label="阶段" value={radar?.strongest_theme?.stage ?? '--'} icon={<RadioTower size={16} />} />
          </div>
        </div>
        <div className="radar-hero-side">
          <button className="refresh-btn" type="button" onClick={() => loadRadar(true)} disabled={loading}>
            <RefreshCcw size={15} />
            {loading ? '同步中' : '刷新'}
          </button>
          <div className="source-card">
            <span>数据源</span>
            <strong>{sourceLabel}</strong>
            <small>{fetchedAt ? `缓存 ${new Date(fetchedAt).toLocaleTimeString('zh-CN')}` : '--'}</small>
            <small>{radar ? `数据 ${new Date(radar.updated_at).toLocaleTimeString('zh-CN')}` : '5 分钟内复用'}</small>
          </div>
        </div>
      </section>

      <section className="radar-grid">
        <article className="panel theme-ranking-panel">
          <h3><BarChart3 size={16} /> 当前题材强度</h3>
          <div className="theme-rank-list">
            {(radar?.themes ?? []).slice(0, 18).map(item => (
              <button
                className={`theme-rank-row ${selected?.name === item.name ? 'active' : ''}`}
                key={`${item.theme_type}-${item.name}`}
                onClick={() => setSelectedName(item.name)}
                type="button"
              >
                <span className="theme-rank-num">{String(item.rank).padStart(2, '0')}</span>
                <span className="theme-rank-main">
                  <strong>{item.name}</strong>
                  <small>{item.theme_type} · {item.stage} · {(item.related_boards ?? []).slice(0, 2).join(' / ')}</small>
                </span>
                <span className="theme-score">{item.score}</span>
                <span className="theme-rank-bar"><i style={{ width: `${item.score}%` }} /></span>
              </button>
            ))}
            {!loading && !radar?.themes.length && <p className="plain-text">暂无题材数据。</p>}
          </div>
        </article>

        <article className="panel selected-theme-panel">
          <h3><Target size={16} /> 板块内部核心</h3>
          {selected ? (
            <>
              <div className="selected-theme-head">
                <div>
                  <strong>{selected.name}</strong>
                  <span>{selected.resonance_tags.join(' / ')}</span>
                  {!!selected.related_boards?.length && <em>{selected.related_boards.slice(0, 6).join(' · ')}</em>}
                </div>
                <b>{selected.stage}</b>
              </div>
              <div className="theme-stat-grid">
                <MiniStat label="涨幅" value={`${fmtSigned(selected.change_pct)}%`} tone={selected.change_pct >= 0 ? 'up' : 'down'} />
                <MiniStat label="净流入" value={`${fmtSigned(selected.net_inflow)}亿`} tone={selected.net_inflow >= 0 ? 'up' : 'down'} />
                <MiniStat label="主力流入" value={`${fmtSigned(selected.main_inflow)}亿`} tone={selected.main_inflow >= 0 ? 'up' : 'down'} />
                <MiniStat label="涨停扩散" value={`${selected.limit_up_count || '--'}`} />
              </div>
              <div className="stock-role-list">
                {selected.core_stocks.map(stock => (
                  <div className="stock-role-row" key={`${stock.role}-${stock.code}-${stock.name}`}>
                    <span className="role-badge">{stock.role}</span>
                    <strong>{stock.name}</strong>
                    <span className={stock.change_pct >= 0 ? 'num-up' : 'num-down'}>{fmtSigned(stock.change_pct)}%</span>
                    <small>{stock.amount ? `${stock.amount.toFixed(2)}亿 · ` : ''}{stock.reason}</small>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="plain-text">等待选择题材。</p>
          )}
        </article>

        <article className="panel theme-flow-panel">
          <h3><TrendingUp size={16} /> 板块资金曲线</h3>
          <ThemeFlowChart items={chartThemes} selectedName={selected?.name ?? null} />
        </article>

        <article className="panel resonance-panel">
          <h3><Layers3 size={16} /> 共振板块</h3>
          <div className="resonance-list">
            {(radar?.resonance ?? []).map(item => (
              <button key={item.name} type="button" onClick={() => setSelectedName(item.name)}>
                <strong>{item.name}</strong>
                <span>{item.resonance_tags.slice(0, 4).join(' · ')}</span>
              </button>
            ))}
            {!radar?.resonance.length && <p className="plain-text">暂无三因子以上共振，控制追高。</p>}
          </div>
        </article>
      </section>

      <section className="radar-bottom-grid">
        <article className="panel">
          <h3><UsersRound size={16} /> 龙头线索</h3>
          <div className="watchlist-tags">
            {(selected?.leader_names ?? []).map(name => <span key={name}>{name}</span>)}
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
        <article className="panel">
          <h3><RadioTower size={16} /> 数据备注</h3>
          <ul className="reason-list">
            {(radar?.notes ?? [error || '等待同步']).slice(0, 4).map((note, i) => <li key={i}>{note}</li>)}
          </ul>
        </article>
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
    const colors = ['#009a6c', '#4472ca', '#c08a35', '#7aa08b', '#b8574f', '#6f7f96', '#8a6d3b', '#5f8c7b']
    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 52, right: 16, top: 18, bottom: 34 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1a241e',
        borderColor: '#3a5244',
        textStyle: { color: '#e8ede7', fontSize: 12 },
        valueFormatter: (v: unknown) => `${Number(v).toFixed(2)} 亿`,
      },
      legend: {
        type: 'scroll',
        bottom: 0,
        left: 'center',
        itemWidth: 10,
        itemHeight: 7,
        textStyle: { color: '#5d685d', fontSize: 11 },
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: xData,
        axisLine: { lineStyle: { color: '#c5c9c0' } },
        axisLabel: {
          color: '#697069',
          fontSize: 10,
          interval: Math.max(0, Math.floor(xData.length / 8) - 1),
        },
      },
      yAxis: {
        type: 'value',
        name: '亿元',
        nameTextStyle: { color: '#8a9186', fontSize: 10 },
        axisLabel: { color: '#697069', fontSize: 10 },
        splitLine: { lineStyle: { color: '#e8ebe4', type: 'dashed' } },
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
            opacity: selectedName && !isSelected ? 0.28 : 0.92,
          },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(0,154,108,0.12)' },
              { offset: 1, color: 'rgba(0,0,0,0)' },
            ]),
            opacity: isSelected ? 0.18 : 0.04,
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
