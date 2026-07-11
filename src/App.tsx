import { useEffect, useState, Suspense, lazy } from 'react'
import { Routes, Route, useNavigate, useLocation, Navigate } from 'react-router-dom'
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
  Menu,
  X,
} from 'lucide-react'
import { API_BASE } from './api'
import './App.css'

const Dashboard = lazy(() => import('./components/Dashboard'))
const FlowDesk = lazy(() => import('./components/FlowDesk'))
const IntelDesk = lazy(() => import('./components/IntelDesk'))
const LimitUpLadder = lazy(() => import('./components/LimitUpLadder'))
const MarketEnv = lazy(() => import('./components/MarketEnv'))
const Positions = lazy(() => import('./components/Positions'))
const NextDayPlans = lazy(() => import('./components/NextDayPlans'))
const TradeLog = lazy(() => import('./components/TradeLog'))
const BuyCheck = lazy(() => import('./components/BuyCheck'))
const ConcentratedAttack = lazy(() => import('./components/ConcentratedAttack'))
const SellPlan = lazy(() => import('./components/SellPlan'))
const MonthlyReview = lazy(() => import('./components/MonthlyReview'))

const navItems = [
  ['题材雷达', Gauge, '/题材雷达'],
  ['资金流证据', BarChart3, '/资金流证据'],
  ['涨停天梯', Flame, '/涨停天梯'],
  ['信息差', Newspaper, '/信息差'],
  ['市场环境', Activity, '/市场环境'],
  ['持仓快照', WalletCards, '/持仓快照'],
  ['次日计划卡', NotebookPen, '/次日计划卡'],
  ['交易日志', ListChecks, '/交易日志'],
  ['买入检查', ClipboardCheck, '/买入检查'],
  ['集中进攻', Target, '/集中进攻'],
  ['卖出执行卡', ShieldAlert, '/卖出执行卡'],
  ['月度复盘', BookOpenCheck, '/月度复盘'],
] as const

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const [backendUp, setBackendUp] = useState(false)
  const [apiStatus, setApiStatus] = useState('检测中')
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const activePath = location.pathname === '/' ? '/题材雷达' : decodeURIComponent(location.pathname)
  const activeLabel = navItems.find(([_, __, path]) => path === activePath)?.[0] ?? '题材雷达'

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
      const item = navItems.find(([label]) => label === detail)
      if (item) {
        navigate(item[2])
        setSidebarOpen(false)
      }
    }
    window.addEventListener('nav', handler)
    return () => window.removeEventListener('nav', handler)
  }, [navigate])

  return (
    <main className="terminal-shell">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="brand" style={{ display: 'flex', width: '100%', alignItems: 'center' }}>
          <span className="brand-mark">TD</span>
          <div style={{ flex: 1 }}>
            <strong>交易纪律系统</strong>
            <span>A 股短线 / 超短线</span>
          </div>
          <button className="hamburger-btn" style={{ marginLeft: 'auto' }} onClick={() => setSidebarOpen(false)}>
            <X size={18} />
          </button>
        </div>
        <nav aria-label="主导航">
          {navItems.map(([label, Icon, path]) => (
            <button
              className={activePath === path ? 'active' : ''}
              key={label}
              onClick={() => {
                navigate(path)
                setSidebarOpen(false)
              }}
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
          <div className="topbar-title-row">
            <button className="hamburger-btn" onClick={() => setSidebarOpen(true)}>
              <Menu size={18} />
            </button>
            <div>
              <span className="eyebrow">Market Discipline Desk</span>
              <h1>{activeLabel}</h1>
            </div>
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

        <Suspense fallback={<div className="loading-fallback">载入中...</div>}>
          <Routes>
            <Route path="/" element={<Navigate to="/题材雷达" replace />} />
            <Route path="/题材雷达" element={<Dashboard />} />
            <Route path="/资金流证据" element={<FlowDesk />} />
            <Route path="/涨停天梯" element={<LimitUpLadder />} />
            <Route path="/信息差" element={<IntelDesk />} />
            <Route path="/市场环境" element={<MarketEnv />} />
            <Route path="/持仓快照" element={<Positions />} />
            <Route path="/次日计划卡" element={<NextDayPlans />} />
            <Route path="/交易日志" element={<TradeLog />} />
            <Route path="/买入检查" element={<BuyCheck />} />
            <Route path="/集中进攻" element={<ConcentratedAttack />} />
            <Route path="/卖出执行卡" element={<SellPlan />} />
            <Route path="/月度复盘" element={<MonthlyReview />} />
            <Route path="*" element={<Navigate to="/题材雷达" replace />} />
          </Routes>
        </Suspense>
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
