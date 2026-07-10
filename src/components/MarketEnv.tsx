import { useEffect, useState } from 'react'
import { API_BASE } from '../api'

type GradeData = {
  grade: string
  total_position_limit: string
  single_position_limit: string
  reasons: string[]
  risk_warnings: string[]
}

const gradeInfo: Record<string, { label: string; color: string; desc: string }> = {
  A: { label: 'A 档 · 进攻', color: 'var(--up)', desc: '主线清晰，龙头强势，赚钱效应好' },
  B: { label: 'B 档 · 正常', color: '#4472ca', desc: '有热点，但分歧较多' },
  C: { label: 'C 档 · 保守', color: 'var(--warn)', desc: '轮动快，追高容易亏' },
  D: { label: 'D 档 · 防守', color: 'var(--down)', desc: '板块退潮，亏钱效应明显' },
}

export default function MarketEnv() {
  const [grade, setGrade] = useState<GradeData | null>(null)
  const [config, setConfig] = useState({
    turnover: '70',
    limitUp: '45',
    leader: '断板承接',
    loss: '一般',
    persistence: '2',
  })

  useEffect(() => { fetchGrade() }, [])

  const fetchGrade = () => {
    const params = new URLSearchParams({
      turnover_score: config.turnover,
      limit_up_count: config.limitUp,
      leader_state: config.leader,
      loss_effect: config.loss,
      theme_persistence_days: config.persistence,
    })
    fetch(`${API_BASE}/api/market/grade?${params}`)
      .then(r => r.json())
      .then(setGrade)
      .catch(() => {})
  }

  const g = grade
  const info = g ? gradeInfo[g.grade] ?? gradeInfo.B : null

  return (
    <div className="env-layout">
      <header className="env-hero">
        <div>
          <h2>市场环境分档</h2>
          <p>根据指数、成交额、热点板块、涨停家数、龙头状态、亏钱效应输出仓位等级。</p>
        </div>
        {g && info && (
          <div className="grade-badge" style={{ borderColor: info.color, background: info.color + '10' }}>
            <span className="grade-letter" style={{ color: info.color }}>{g.grade}</span>
            <div>
              <strong>{info.label}</strong>
              <small>{info.desc}</small>
            </div>
          </div>
        )}
      </header>

      <div className="env-grid">
        <div className="panel env-config">
          <h3>参数调整</h3>
          <div className="config-grid">
            <label>
              成交额评分
              <input type="range" min="0" max="100" value={config.turnover} onChange={e => setConfig(p => ({ ...p, turnover: e.target.value }))} />
              <span>{config.turnover}</span>
            </label>
            <label>
              涨停家数
              <input type="range" min="0" max="100" value={config.limitUp} onChange={e => setConfig(p => ({ ...p, limitUp: e.target.value }))} />
              <span>{config.limitUp}</span>
            </label>
            <label>
              龙头状态
              <select value={config.leader} onChange={e => setConfig(p => ({ ...p, leader: e.target.value }))}>
                {['强势连板', '断板承接', '放量滞涨', '高位大跌', '无明显龙头'].map(v => <option key={v}>{v}</option>)}
              </select>
            </label>
            <label>
              亏钱效应
              <select value={config.loss} onChange={e => setConfig(p => ({ ...p, loss: e.target.value }))}>
                {['极弱', '一般', '中等', '明显', '严重'].map(v => <option key={v}>{v}</option>)}
              </select>
            </label>
            <label>
              主线持续天数
              <select value={config.persistence} onChange={e => setConfig(p => ({ ...p, persistence: e.target.value }))}>
                {['0', '1', '2', '3', '4', '5'].map(v => <option key={v}>{v}</option>)}
              </select>
            </label>
          </div>
          <button className="grade-btn" onClick={fetchGrade}>重新评估</button>
        </div>

        {g && (
          <>
            <div className="panel">
              <h3>仓位限制</h3>
              <div className="pos-limits">
                <div className="pos-limit">
                  <span>总仓位上限</span>
                  <strong>{g.total_position_limit}</strong>
                </div>
                <div className="pos-limit">
                  <span>单股仓位上限</span>
                  <strong>{g.single_position_limit}</strong>
                </div>
              </div>
            </div>
            <div className="panel">
              <h3>判断依据</h3>
              <ul className="reason-list">
                {g.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
            <div className="panel">
              <h3>风险警告</h3>
              {g.risk_warnings.length > 0 ? (
                <ul className="reason-list warn">
                  {g.risk_warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              ) : (
                <p className="plain-text">未触发风险警告。</p>
              )}
            </div>
            <div className="panel env-table-panel">
              <h3>分档参考</h3>
              <table className="ref-table">
                <thead>
                  <tr><th>档位</th><th>判断标准</th><th>总仓位</th><th>单股仓位</th></tr>
                </thead>
                <tbody>
                  <tr className={g.grade === 'A' ? 'active' : ''}>
                    <td><span className="grade-dot" style={{ background: 'var(--up)' }} />A 进攻</td>
                    <td>主线清晰，龙头强，赚钱效应好</td>
                    <td>50%-80%</td>
                    <td>30%-40% 龙一</td>
                  </tr>
                  <tr className={g.grade === 'B' ? 'active' : ''}>
                    <td><span className="grade-dot" style={{ background: '#4472ca' }} />B 正常</td>
                    <td>有热点，但分歧较多</td>
                    <td>30%-50%</td>
                    <td>25%-30%</td>
                  </tr>
                  <tr className={g.grade === 'C' ? 'active' : ''}>
                    <td><span className="grade-dot" style={{ background: 'var(--warn)' }} />C 保守</td>
                    <td>轮动快，追高容易亏</td>
                    <td>0%-30%</td>
                    <td>10%-20%</td>
                  </tr>
                  <tr className={g.grade === 'D' ? 'active' : ''}>
                    <td><span className="grade-dot" style={{ background: 'var(--down)' }} />D 防守</td>
                    <td>退潮，亏钱效应明显</td>
                    <td>0%-10%</td>
                    <td>不开新仓</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
