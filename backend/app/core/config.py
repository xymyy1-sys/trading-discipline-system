from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Trading Discipline System"
    database_url: str = Field(
        default="sqlite:///./data/trading_discipline.db",
        validation_alias="DATABASE_URL",
    )
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://1.12.222.27:5173",
    ]
    data_dir: Path = Path("data")
    auth_enabled: bool = Field(default=True, validation_alias="AUTH_ENABLED")
    auth_username: str = Field(default="admin", validation_alias="AUTH_USERNAME")
    auth_password: str = Field(default="", validation_alias="AUTH_PASSWORD")
    auth_secret: str = Field(default="", validation_alias="AUTH_SECRET")
    auth_session_hours: int = Field(default=12, validation_alias="AUTH_SESSION_HOURS")
    auth_cookie_secure: bool = Field(default=False, validation_alias="AUTH_COOKIE_SECURE")

    def validate_security(self) -> None:
        if not self.auth_enabled:
            return
        if len(self.auth_password) < 12:
            raise RuntimeError("AUTH_PASSWORD must contain at least 12 characters")
        if len(self.auth_secret) < 32:
            raise RuntimeError("AUTH_SECRET must contain at least 32 characters")


@lru_cache
def get_settings() -> Settings:
    return Settings()
