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


@lru_cache
def get_settings() -> Settings:
    return Settings()
