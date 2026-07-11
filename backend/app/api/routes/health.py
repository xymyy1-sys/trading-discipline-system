from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()
root_router = APIRouter()

@root_router.get("/", response_class=HTMLResponse)
def root() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>交易纪律系统 API</title>
<style>
  body { font-family: system-ui,sans-serif; max-width:640px; margin:60px auto; padding:0 20px; color:#17231f; background:#f7f7f1; }
  h1 { font-size:24px; }
  a { color:#4472ca; }
  code { background:#e5e8df; padding:2px 6px; border-radius:4px; font-size:13px; }
  ul { line-height:2; }
</style>
</head>
<body>
<h1>📊 交易纪律系统 API</h1>
<p>后端运行中。前端请访问 <a href="http://1.12.222.27:5173">http://1.12.222.27:5173</a></p>
<h2>接口列表</h2>
<ul>
  <li><code>GET /api/health</code></li>
  <li><code>GET /api/market/sector-flow</code></li>
  <li><code>GET /api/market/sector-detail</code></li>
  <li><code>GET /api/market/limit-up-ladder</code></li>
  <li><code>GET /api/market/theme-radar</code></li>
  <li><code>GET /api/market/grade</code></li>
  <li><code>GET /api/intel/daily</code></li>
  <li><code>POST /api/checks/pre-trade</code></li>
  <li><code>GET /api/holdings</code> · <code>POST /api/holdings</code></li>
  <li><code>GET /api/next-day-plans</code> · <code>POST /api/next-day-plans/generate</code></li>
  <li><code>GET /api/trades</code> · <code>POST /api/trades</code></li>
  <li><code>GET /api/exit-cards</code> · <code>POST /api/exit-cards</code></li>
  <li><code>GET /api/sell-plans</code></li>
</ul>
</body>
</html>"""

@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
