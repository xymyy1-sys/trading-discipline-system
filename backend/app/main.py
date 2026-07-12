from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.api.routes import root_router, router
from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine
from app.core.limiter import limiter
from app.services.intraday_collector import start_intraday_collector, stop_intraday_collector
from app.services.audit import record_audit
import uuid

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_security()
    Base.metadata.create_all(bind=engine)
    start_intraday_collector()
    yield
    await stop_intraday_collector()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.middleware("http")
async def audit_write_requests(request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if settings.audit_enabled and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        db = SessionLocal()
        try:
            record_audit(db, getattr(request.state, "auth_user", "anonymous"), request.method, request.url.path, response.status_code, request_id)
        except Exception:
            db.rollback()
        finally:
            db.close()
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(root_router)
app.include_router(router)
