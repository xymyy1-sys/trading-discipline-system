from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "知行交易驾驶舱"
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
    demo_username: str = Field(default="demo", validation_alias="DEMO_USERNAME")
    demo_password: str = Field(default="", validation_alias="DEMO_PASSWORD")
    demo_database_url: str = Field(default="sqlite:///./data/demo_discipline.db", validation_alias="DEMO_DATABASE_URL")
    audit_enabled: bool = Field(default=True, validation_alias="AUDIT_ENABLED")
    ai_api_key: str = Field(default="", validation_alias="AI_API_KEY")
    ai_model: str = Field(default="deepseek-reasoner", validation_alias="AI_MODEL")
    ai_base_url: str = Field(default="https://api.deepseek.com", validation_alias="AI_BASE_URL")
    ai_provider: str = Field(default="deepseek", validation_alias="AI_PROVIDER")
    dingtalk_enabled: bool = Field(default=False, validation_alias="DINGTALK_ENABLED")
    dingtalk_webhook: str = Field(default="", validation_alias="DINGTALK_WEBHOOK")
    dingtalk_secret: str = Field(default="", validation_alias="DINGTALK_SECRET")

    def validate_security(self) -> None:
        if not self.auth_enabled:
            return
        if len(self.auth_password) < 12:
            raise RuntimeError("AUTH_PASSWORD must contain at least 12 characters")
        if len(self.auth_secret) < 32:
            raise RuntimeError("AUTH_SECRET must contain at least 32 characters")
        if self.demo_password and len(self.demo_password) < 8:
            raise RuntimeError("DEMO_PASSWORD must contain at least 8 characters")


@lru_cache
def get_settings() -> Settings:
    return Settings()
