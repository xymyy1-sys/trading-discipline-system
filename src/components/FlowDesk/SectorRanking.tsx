import { ChevronRight, TrendingDown, TrendingUp } from 'lucide-react'
import type { SectorFlowItem } from '../../types'

function displayName(item: SectorFlowItem) {
  return item.display_name || item.raw_name || item.name
}

function categoryLine(item: SectorFlowItem) {
  const category = item.category || '其他'
  const subline = item.subline || item.mainline || item.theme_line || item.raw_name || item.name
  return category === subline ? category : `${category} / ${subline}`
}

export default function SectorRanking({
  title,
  items,
  direction,
  selected,
  onSelect,
  onOpenDetail,
}: {
  title: string
  items: SectorFlowItem[]
  direction: 'in' | 'out'
  selected: string | null
  onSelect: (name: string | null) => void
  onOpenDetail: (item: SectorFlowItem) => void
}) {
  const color = direction === 'in' ? '#d92d20' : '#00875a'
  return (
    <div className="ranking-full">
      <h3>
        {direction === 'in' ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
        {title}
      </h3>
      <div className="ranking-full-list">
        {items.slice(0, 18).map((item, i) => (
          <button
            className={`rank-row-full ${selected === displayName(item) ? 'picked' : ''}`}
            key={item.name}
            type="button"
            onClick={() => {
              onSelect(displayName(item))
              onOpenDetail(item)
            }}
          >
            <span className="rank-num">{String(i + 1).padStart(2, '0')}</span>
            <span className="rank-name">{displayName(item)}</span>
            <span className="rank-theme-line">{categoryLine(item)}</span>
            <span className="rank-stats">
              <span className={direction === 'in' ? 'num-up' : 'num-down'}>
                {item.net_inflow >= 0 ? '+' : ''}{item.net_inflow.toFixed(2)}亿
              </span>
              <span className="rank-pct" style={{ color: item.change_pct >= 0 ? 'var(--up)' : 'var(--down)' }}>
                {item.change_pct >= 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
              </span>
            </span>
            <span className="rank-bar">
              <i style={{ width: `${Math.max(4, item.strength)}%`, background: color }} />
            </span>
            {item.flow_breakdown?.length > 0 && (
              <span className="rank-flow-breakdown" aria-label="东方财富资金拆解">
                {item.flow_breakdown.slice(0, 4).map(part => (
                  <i className={part.net >= 0 ? 'in' : 'out'} key={part.name}>
                    {part.name}{part.net >= 0 ? '+' : ''}{part.net.toFixed(1)}
                  </i>
                ))}
              </span>
            )}
            {item.leaders.filter(l => l !== '待识别').length > 0 && (
              <span className="rank-leaders">{item.leaders.filter(l => l !== '待识别').join(' · ')}</span>
            )}
            <ChevronRight size={12} className="rank-arrow" />
          </button>
        ))}
      </div>
    </div>
  )
}
