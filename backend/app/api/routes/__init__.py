from fastapi import APIRouter
from app.api.routes.health import router as health_router
from app.api.routes.market import router as market_router
from app.api.routes.holdings import router as holdings_router
from app.api.routes.trades import router as trades_router
from app.api.routes.plans import router as plans_router
from app.api.routes.checks import router as checks_router
from app.api.routes.stocks import router as stocks_router

# Root router for HTML/root paths
from app.api.routes.health import root_router

# Core API router
router = APIRouter(prefix="/api")
router.include_router(health_router)
router.include_router(market_router)
router.include_router(holdings_router)
router.include_router(trades_router)
router.include_router(plans_router)
router.include_router(checks_router)
router.include_router(stocks_router)
