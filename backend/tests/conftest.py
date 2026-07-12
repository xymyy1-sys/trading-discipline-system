import os

os.environ.setdefault("AUTH_PASSWORD", "test-password-only")
os.environ.setdefault("AUTH_SECRET", "test-secret-must-be-at-least-32-characters")
os.environ.setdefault("AUDIT_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db

# Setup an in-memory SQLite database for testing
DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(name="db_session")
def fixture_db_session():
    # Create the database tables
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(name="client")
def fixture_client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    # Override get_db dependency in app
    app.dependency_overrides[get_db] = override_get_db
    from app.core.security import require_auth
    app.dependency_overrides[require_auth] = lambda: "test-user"
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
