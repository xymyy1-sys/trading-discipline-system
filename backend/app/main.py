from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from app.api.routes import root_router, router
from app.core.config import get_settings
from app.core.database import Base, engine

settings = get_settings()

Base.metadata.create_all(bind=engine)


def _ensure_lightweight_schema() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if not inspector.has_table("next_day_plans"):
        plan_columns = set()
    else:
        plan_columns = {column["name"] for column in inspector.get_columns("next_day_plans")}
    additions = {}
    if plan_columns:
        additions.update({
            "plan_type": "ALTER TABLE next_day_plans ADD COLUMN plan_type VARCHAR(24) DEFAULT 'holding'",
            "limit_up_price": "ALTER TABLE next_day_plans ADD COLUMN limit_up_price FLOAT DEFAULT 0",
            "auction_plan": "ALTER TABLE next_day_plans ADD COLUMN auction_plan TEXT DEFAULT '{}'",
        })
    review_columns = (
        {column["name"] for column in inspector.get_columns("trade_reviews")}
        if inspector.has_table("trade_reviews")
        else set()
    )
    review_additions = {
        "status": "ALTER TABLE trade_reviews ADD COLUMN status VARCHAR(16) DEFAULT 'done'",
        "error_message": "ALTER TABLE trade_reviews ADD COLUMN error_message TEXT DEFAULT ''",
    }
    with engine.begin() as conn:
        for column, statement in additions.items():
            if column not in plan_columns:
                conn.execute(text(statement))
        for column, statement in review_additions.items():
            if column not in review_columns:
                conn.execute(text(statement))


_ensure_lightweight_schema()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(root_router)
app.include_router(router)
