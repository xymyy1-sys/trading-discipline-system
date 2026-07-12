from fastapi import APIRouter, Depends
from app.api.routes.health import router as health_router
from app.api.routes.market import router as market_router
from app.api.routes.holdings import router as holdings_router
from app.api.routes.trades import router as trades_router
from app.api.routes.plans import router as plans_router
from app.api.routes.checks import router as checks_router
from app.api.routes.stocks import router as stocks_router
from app.api.routes.auth import router as auth_router
from app.api.routes.strategies import router as strategies_router
from app.api.routes.acceptance import router as acceptance_router
from app.core.security import require_auth

# Root router for HTML/root paths
from app.api.routes.health import root_router

# Core API router
router = APIRouter(prefix="/api")
router.include_router(health_router)
router.include_router(auth_router)

protected_router = APIRouter(dependencies=[Depends(require_auth)])
protected_router.include_router(market_router)
protected_router.include_router(holdings_router)
protected_router.include_router(trades_router)
protected_router.include_router(plans_router)
protected_router.include_router(checks_router)
protected_router.include_router(stocks_router)
protected_router.include_router(strategies_router)
protected_router.include_router(acceptance_router)
router.include_router(protected_router)
