import { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  BarChart3,
  BookOpenCheck,
  ClipboardCheck,
  Flame,
  Gauge,
  NotebookPen,
  ListChecks,
  Newspaper,
  ShieldAlert,
  Target,
  WalletCards,
} from 'lucide-react'
import Dashboard from './components/Dashboard'
import FlowDesk from './components/FlowDesk'
import IntelDesk from './components/IntelDesk'
import LimitUpLadder from './components/LimitUpLadder'
import MarketEnv from './components/MarketEnv'
import Positions from './components/Positions'
import TradeLog from './components/TradeLog'
import BuyCheck from './components/BuyCheck'
import ConcentratedAttack from './components/ConcentratedAttack'
import SellPlan from './components/SellPlan'
import MonthlyReview from './components/MonthlyReview'
import NextDayPlans from './components/NextDayPlans'
import { API_BASE } from './api'
import './App.css'

const navItems = [
  ['题材雷达', Gauge],
  ['资金流证据', BarChart3],
  ['涨停天梯', Flame],
  ['信息差', Newspaper],
  ['市场环境', Activity],
  ['持仓快照', WalletCards],
  ['次日计划卡', NotebookPen],
  ['交易日志', ListChecks],
  ['买入检查', ClipboardCheck],
  ['集中进攻', Target],
  ['卖出执行卡', ShieldAlert],
  ['月度复盘', BookOpenCheck],
] as const

type NavLabel = (typeof navItems)[number][0]

export default function App() {
  const [activeView, setActiveView] = useState<NavLabel>('题材雷达')
  const [backendUp, setBackendUp] = useState(false)
  const [apiStatus, setApiStatus] = useState('检测中')

  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then(r => r.json())
      .then(() => {
        setBackendUp(true)
        setApiStatus('已连接')
      })
      .catch(() => {
        setBackendUp(false)
        setApiStatus('未启动')
      })
  }, [])

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail
      if (navItems.some(([l]) => l === detail)) {
        setActiveView(detail as NavLabel)
      }
    }
    window.addEventListener('nav', handler)
    return () => window.removeEventListener('nav', handler)
  }, [])

  const renderPage = useCallback(() => {
    switch (activeView) {
      case '题材雷达': return <Dashboard />
      case '资金流证据': return <FlowDesk />
      case '涨停天梯': return <LimitUpLadder />
      case '信息差': return <IntelDesk />
      case '市场环境': return <MarketEnv />
      case '持仓快照': return <Positions />
      case '次日计划卡': return <NextDayPlans />
      case '交易日志': return <TradeLog />
      case '买入检查': return <BuyCheck />
      case '集中进攻': return <ConcentratedAttack />
      case '卖出执行卡': return <SellPlan />
      case '月度复盘': return <MonthlyReview />
      default: return <Dashboard />
    }
  }, [activeView])

  return (
    <main className="terminal-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">TD</span>
          <div>
            <strong>交易纪律系统</strong>
            <span>A 股短线 / 超短线</span>
          </div>
        </div>
        <nav aria-label="主导航">
          {navItems.map(([label, Icon]) => (
            <button
              className={activeView === label ? 'active' : ''}
              key={label}
              onClick={() => setActiveView(label)}
              type="button"
              title={label}
            >
              <Icon size={17} strokeWidth={1.8} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="discipline-strip">
          <span>今日模式</span>
          <strong>标准短线</strong>
          <small>单票上限 40% · 亏损仓禁止补仓</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <span className="eyebrow">Market Discipline Desk</span>
            <h1>{activeView}</h1>
          </div>
          <div className="status-cluster">
            <Metric label="市场档位" value="--" tone="neutral" />
            <Metric label="总仓上限" value="--" tone="neutral" />
            <Metric
              label="后端"
              value={apiStatus}
              tone={backendUp ? 'neutral' : 'muted'}
            />
          </div>
        </header>

        {renderPage()}
      </section>
    </main>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone: 'neutral' | 'muted' }) {
  return (
    <div className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
