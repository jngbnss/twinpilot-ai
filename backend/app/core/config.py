from functools import cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TWINPILOT_", env_file=".env", extra="ignore")

    runs_dir: Path = Path("runs")
    db_path: Path = Path("runs/index.sqlite")
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    model_path: Path = Path("ai/models/isolation_forest.joblib")
    tick_hz: float = 50.0
    broadcast_hz: float = 10.0
    max_live_duration_s: float = 900.0
    history_seconds: float = 120.0


@cache
def get_settings() -> Settings:
    return Settings()
