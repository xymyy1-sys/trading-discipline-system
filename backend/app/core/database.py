from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from fastapi import Request

from app.core.config import get_settings


settings = get_settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)

from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

demo_engine = create_engine(
    settings.demo_database_url,
    connect_args={"check_same_thread": False} if settings.demo_database_url.startswith("sqlite") else {},
)

@event.listens_for(demo_engine, "connect")
def set_demo_sqlite_pragma(dbapi_connection, connection_record):
    if settings.demo_database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

DemoSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=demo_engine)


class Base(DeclarativeBase):
    pass


def get_db(request: Request) -> Generator[Session, None, None]:
    is_demo = getattr(request.state, "auth_user", "") == settings.demo_username and bool(settings.demo_password)
    db = DemoSessionLocal() if is_demo else SessionLocal()
    try:
        yield db
    finally:
        db.close()
