import { useEffect, useState } from 'react'
import { TrendingUp, TrendingDown, Target, Activity } from 'lucide-react'
import { API_BASE } from '../api'

type Trade = {
  id: number
  side: string
  amount: number
  compliant: boolean
  human_tags: string[]
}

type ReviewStats = {
  totalTrades: number
  winRate: number
  avgWin: number
  avgLoss: number
  profitLossRatio: number
  maxDrawdown: number
  systemCompliantRate: number
  dangerousWinCount: number
  systemLossCount: number
  tagFrequency: Record<string, number>
  score: number
}

export default function MonthlyReview() {
  const [stats, setStats] = useState<ReviewStats | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetch(`${API_BASE}/api/trades`)
      .then(r => r.json())
      .then((trades: Trade[]) => {
        const wins = trades.filter(t => t.amount > 0)
        const losses = trades.filter(t => t.amount <= 0)
        const compliant = trades.filter(t => t.compliant)
        const dangerous = trades.filter(t => !t.compliant && t.amount > 0)
        const systemLoss = trades.filter(t => !t.compliant && t.amount <= 0)

        const tags: Record<string, number> = {}
        trades.forEach(t => t.human_tags.forEach(tg => { tags[tg] = (tags[tg] || 0) + 1 }))

        const winRate = trades.length > 0 ? (wins.length / trades.length) * 100 : 0
        const avgWin = wins.length > 0 ? wins.reduce((s, t) => s + t.amount, 0) / wins.length : 0
        const avgLoss = losses.length > 0 ? Math.abs(losses.reduce((s, t) => s + t.amount, 0) / losses.length) : 0
        const plr = avgLoss > 0 ? avgWin / avgLoss : 0
        const compliantRate = trades.length > 0 ? (compliant.length / trades.length) * 100 : 0

        let score = 50
        score += Math.min(20, winRate * 0.3)
        score += Math.min(15, plr * 8)
        score += Math.min(10, compliantRate * 0.15)
        score -= dangerous.length * 6
        score -= systemLoss.length * 10
        score += trades.length > 0 ? 5 : 0
        score = Math.max(0, Math.min(100, score))

        setStats({
          totalTrades: trades.length,
          winRate: Math.round(winRate * 10) / 10,
          avgWin: Math.round(avgWin),
          avgLoss: Math.round(avgLoss),
          profitLossRatio: Math.round(plr * 100) / 100,
          maxDrawdown: Math.round(losses.reduce((s, t) => s + Math.abs(t.amount), 0)),
          systemCompliantRate: Math.round(compliantRate * 10) / 10,
          dangerousWinCount: dangerous.length,
          systemLossCount: systemLoss.length,
          tagFrequency: tags,
          score: Math.round(score),
        })
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="panel"><p className="plain-text">加载中...</p></div>
  if (!stats || stats.totalTrades === 0) {
    return (
      <div className="env-hero">
        <div>
          <h2>月度复盘</h2>
          <p>统计胜率、平均盈亏、盈亏比、最大回撤、体系内交易占比、危险盈利、体系外亏损、人性弱点频率，并给出纪律评分。</p>
        </div>
        <div className="panel"><p className="plain-text">暂无交易记录，无法生成月度复盘。请先在"交易日志"中录入交易。</p></div>
      </div>
    )
  }

  const scoreColor = stats.score >= 80 ? 'var(--up)' : stats.score >= 60 ? 'var(--warn)' : 'var(--down)'

  return (
    <div className="review-layout">
      <header className="env-hero">
        <div>
          <h2>月度复盘</h2>
          <p>胜率、平均盈亏、盈亏比、最大回撤、体系内交易占比、危险盈利、体系外亏损、人性弱点频率。</p>
        </div>
        <div className="score-badge" style={{ borderColor: scoreColor }}>
          <span className="score-num" style={{ color: scoreColor }}>{stats.score}</span>
          <span>纪律评分 / 100</span>
        </div>
      </header>

      <div className="review-grid">
        <div className="review-metrics">
          <div className="review-metric">
            <Activity size={18} />
            <strong>{stats.totalTrades}</strong>
            <span>总交易</span>
          </div>
          <div className="review-metric">
            <TrendingUp size={18} />
            <strong style={{ color: 'var(--up)' }}>{stats.winRate}%</strong>
            <span>胜率</span>
          </div>
          <div className="review-metric">
            <TrendingUp size={18} />
            <strong style={{ color: 'var(--up)' }}>+{stats.avgWin.toLocaleString()}</strong>
            <span>均盈利</span>
          </div>
          <div className="review-metric">
            <TrendingDown size={18} />
            <strong style={{ color: 'var(--down)' }}>-{stats.avgLoss.toLocaleString()}</strong>
            <span>均亏损</span>
          </div>
          <div className="review-metric">
            <Target size={18} />
            <strong>{stats.profitLossRatio}</strong>
            <span>盈亏比</span>
          </div>
          <div className="review-metric">
            <TrendingDown size={18} />
            <strong style={{ color: 'var(--down)' }}>{stats.maxDrawdown.toLocaleString()}</strong>
            <span>累计亏损</span>
          </div>
        </div>

        <div className="review-cards">
          <div className="panel">
            <h3>体系合规</h3>
            <div className="compliance-bars">
              <div>
                <span>体系内交易占比</span>
                <div className="bar-track"><div className="bar-fill green" style={{ width: `${stats.systemCompliantRate}%` }} /></div>
                <strong>{stats.systemCompliantRate}%</strong>
              </div>
              <div>
                <span>危险盈利次数</span>
                <strong style={{ color: stats.dangerousWinCount > 0 ? 'var(--down)' : 'var(--up)' }}>{stats.dangerousWinCount}</strong>
              </div>
              <div>
                <span>体系外亏损次数</span>
                <strong style={{ color: stats.systemLossCount > 0 ? 'var(--down)' : 'var(--up)' }}>{stats.systemLossCount}</strong>
              </div>
            </div>
          </div>
          <div className="panel">
            <h3>人性弱点频率</h3>
            {Object.keys(stats.tagFrequency).length === 0 ? (
              <p className="plain-text">未标记人性弱点。</p>
            ) : (
              <div className="tag-freq">
                {Object.entries(stats.tagFrequency)
                  .sort((a, b) => b[1] - a[1])
                  .map(([tag, count]) => (
                    <div key={tag}>
                      <span>{tag}</span>
                      <div className="bar-track"><div className="bar-fill red" style={{ width: `${Math.min(100, count * 20)}%` }} /></div>
                      <strong>{count}</strong>
                    </div>
                  ))}
              </div>
            )}
          </div>
        </div>

        <div className="panel review-advice">
          <h3>复盘建议</h3>
          <div className="rule-list">
            {stats.score < 60 && <span>纪律评分偏低，建议暂停主动进攻，先复盘最近 5 笔交易。</span>}
            {stats.dangerousWinCount >= 3 && <span>危险盈利较多，违反规则的盈利不能强化为经验。</span>}
            {stats.systemLossCount >= 4 && <span>体系外亏损过多，回到标准短线模式，集中进攻暂停。</span>}
            {stats.winRate < 35 && <span>胜率偏低，检查是否在主线前排操作，避免后排跟风和杂毛。</span>}
            {stats.profitLossRatio < 1 && <span>盈亏比小于 1，检查止损执行和止盈计划是否到位。</span>}
            {stats.score >= 80 && <span>纪律执行较好，可在主线确认的前提下评估集中进攻机会。</span>}
          </div>
        </div>
      </div>
    </div>
  )
}
