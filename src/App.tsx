import { useEffect, useState, Suspense, lazy } from 'react'
import { Routes, Route, useNavigate, useLocation, Navigate } from 'react-router-dom'
import {
  Activity,
  BookOpenCheck,
  Flame,
  Target,
  WalletCards,
  Crosshair,
  Menu,
  X,
} from 'lucide-react'
import { API_BASE } from './api'
import './App.css'
import { TodayDecisionSummary, WorkspacePage } from './components/workspaces/WorkspacePages'

const Dashboard = lazy(() => import('./components/Dashboard'))
const FlowDesk = lazy(() => import('./components/FlowDesk'))
const IntelDesk = lazy(() => import('./components/IntelDesk'))
const LimitUpLadder = lazy(() => import('./components/LimitUpLadder'))
const MarketEnv = lazy(() => import('./components/MarketEnv'))
const Positions = lazy(() => import('./components/Positions'))
const DecisionCard = lazy(() => import('./components/DecisionCard'))
const NextDayPlans = lazy(() => import('./components/NextDayPlans'))
const TradeLog = lazy(() => import('./components/TradeLog'))
const MonthlyReview = lazy(() => import('./components/MonthlyReview'))
const ReviewCalibration = lazy(() => import('./components/ReviewCalibration'))
const CandidatePool = lazy(() => import('./components/CandidatePool'))
const StrategyTemplates = lazy(() => import('./components/StrategyTemplates'))
const HistoricalReplay = lazy(() => import('./components/HistoricalReplay'))

const navItems = [
  ['今日决策', Activity, '/今日决策'],
  ['选股中心', Target, '/选股中心'],
  ['打板预期', Flame, '/打板预期'],
  ['持仓执行', WalletCards, '/持仓执行'],
  ['复盘校准', BookOpenCheck, '/复盘校准'],
] as const

const oldPathToWorkspace: Record<string, string> = {
  '/题材雷达': '/选股中心',
  '/资金流证据': '/选股中心',
  '/涨停天梯': '/选股中心',
  '/信息差': '/今日决策',
  '/市场环境': '/今日决策',
  '/持仓快照': '/持仓执行',
  '/个股决策卡': '/选股中心',
  '/次日计划卡': '/打板预期',
  '/交易日志': '/复盘校准',
  '/买入检查': '/选股中心',
  '/集中进攻': '/选股中心',
  '/卖出执行卡': '/持仓执行',
  '/月度复盘': '/复盘校准',
}

const routeLabels: Record<string, string> = Object.fromEntries([
  ...navItems.map(([label, _, path]) => [path, label]),
])

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const [backendUp, setBackendUp] = useState(false)
  const [apiStatus, setApiStatus] = useState('检测中')
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const activePath = location.pathname === '/' ? '/今日决策' : decodeURIComponent(location.pathname)
  const activeWorkspacePath = oldPathToWorkspace[activePath] ?? activePath
  const activeLabel = routeLabels[activePath] ?? routeLabels[activeWorkspacePath] ?? '今日决策'

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
            <span className="brand-mark" aria-hidden="true">
              <Crosshair size={22} strokeWidth={1.8} />
            </span>
          <div style={{ flex: 1 }}>
            <strong>交易纪律系统</strong>
            <span>A 股交易纪律工作台</span>
          </div>
          <button className="hamburger-btn" style={{ marginLeft: 'auto' }} onClick={() => setSidebarOpen(false)}>
            <X size={18} />
          </button>
        </div>
        <nav aria-label="主导航">
          {navItems.map(([label, Icon, path]) => (
            <button
              className={activeWorkspacePath === path ? 'active' : ''}
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
              <span className="eyebrow">交易纪律工作台</span>
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
            <Route path="/" element={<Navigate to="/今日决策" replace />} />
            <Route path="/今日决策" element={<TodayDecisionWorkspace />} />
            <Route path="/选股中心" element={<StockSelectionWorkspace />} />
            <Route path="/打板预期" element={<LimitExpectationWorkspace />} />
            <Route path="/持仓执行" element={<PositionExecutionWorkspace />} />
            <Route path="/复盘校准" element={<ReviewCalibrationWorkspace />} />
            {Object.entries(oldPathToWorkspace).map(([path, destination]) => (
              <Route key={path} path={path} element={<Navigate to={destination} replace />} />
            ))}
            <Route path="*" element={<Navigate to="/今日决策" replace />} />
          </Routes>
        </Suspense>
      </section>
    </main>
  )
}

function TodayDecisionWorkspace() {
  return (
    <WorkspacePage
      title="今日决策中心"
      subtitle="盘中决策与风险处置"
      objective="先回答今天该做什么、什么不能做、哪些持仓必须处理，再进入具体模块。"
      allowed={['处理持仓风险', '按市场档位选择策略', '只执行有证据的计划']}
      forbidden={['风险未解除前扩大仓位', '无计划追高', '亏损仓补仓摊低成本']}
      modules={[
        { key: 'market', label: '市场环境', description: '指数、情绪周期、进攻/防守档位', Component: MarketEnv },
        { key: 'positions', label: '持仓风险', description: '持仓快照、执行状态、风险排行', Component: Positions },
        { key: 'intel', label: '信息差', description: '盘中消息与待验证线索', Component: IntelDesk },
      ]}
    >
      <TodayDecisionSummary />
    </WorkspacePage>
  )
}

function StockSelectionWorkspace() {
  return (
    <WorkspacePage
      title="选股中心"
      subtitle="盘前发现与观察池管理"
      objective="从主线、题材强度、资金与涨停质量中发现新标的，形成少而精的观察池。"
      allowed={['主线前排', '资金确认', '量价健康', '预期差不为负']}
      forbidden={['后排跟风', '数据质量不合格', '高位巨量滞涨', '板块资金持续转弱']}
      modules={[
        { key: 'candidates', label: '自动观察池', description: '主线、涨停质量、资金与风险综合推荐', Component: CandidatePool },
        { key: 'radar', label: '主线题材', description: '主线强度、共振方向、核心股', Component: Dashboard },
        { key: 'flow', label: '资金证据', description: '板块资金拐点、排名与强弱', Component: FlowDesk },
        { key: 'ladder', label: '涨停质量', description: '连板高度、封板质量、题材聚类', Component: LimitUpLadder },
        { key: 'card', label: '个股研判', description: '预期、实际、事件与失效条件', Component: DecisionCard },
      ]}
    />
  )
}

function LimitExpectationWorkspace() {
  return (
    <WorkspacePage
      title="打板预期中心"
      subtitle="盘前预期与条件单预案"
      objective="围绕涨停质量、次日合理预期、竞价验证、开盘确认和冲板/炸板风险组织打板决策。"
      allowed={['强预期且量价确认', '前排助攻充分', '封板质量可解释']}
      forbidden={['竞价严重低于预期仍买入', '炸板后无修复继续幻想', '弱预期不降仓']}
      modules={[
        { key: 'plans', label: '预期与执行预案', description: '合理开盘、竞价验证、三套剧本和失效条件', Component: NextDayPlans },
      ]}
    />
  )
}

function PositionExecutionWorkspace() {
  return (
    <WorkspacePage
      title="持仓执行中心"
      subtitle="盘中持仓与风险执行"
      objective="围绕原始买入逻辑、当前预期、利润保护、止损线、资金跷跷板和做T计划管理持仓。"
      allowed={['继续持有有证据', '按状态机减仓', '只在逻辑成立时做T']}
      forbidden={['预期证伪后补仓', '用做T掩盖止损', '利润保护失效仍等待回本']}
      modules={[
        { key: 'positions', label: '持仓与执行', description: '盈亏、状态机、止损、减仓和做T约束', Component: Positions },
      ]}
    />
  )
}

function ReviewCalibrationWorkspace() {
  return (
    <WorkspacePage
      title="复盘校准中心"
      subtitle="盘后复盘与规则校准"
      objective="先沉淀计划、执行、偏差和纪律记录；P2 再接入模型有效性统计和参数自动校准。"
      allowed={['记录真实执行', '复盘计划偏差', '统计纪律问题']}
      forbidden={['只复盘盈亏', '忽略未执行提醒', '用主观判断覆盖证据']}
      modules={[
        { key: 'replay', label: '历史回放', description: '单股盘中事件与操作建议时间线', Component: HistoricalReplay },
        { key: 'strategies', label: '交易规则', description: '可编辑、可版本化的交易剧本', Component: StrategyTemplates },
        { key: 'calibration', label: '执行校准', description: '计划偏差、执行反馈、纪律缺口', Component: ReviewCalibration },
        { key: 'trades', label: '交易日志', description: '交易记录、深度复盘、执行原因', Component: TradeLog },
        { key: 'month', label: '月度复盘', description: '月度纪律、盈亏结构、改进建议', Component: MonthlyReview },
      ]}
    />
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
