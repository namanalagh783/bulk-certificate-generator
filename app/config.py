"""Application settings, loaded from environment variables and/or a .env file.

Centralising configuration here (instead of reading os.environ in various modules) keeps the
rest of the codebase testable: every module that needs a setting receives a `Settings` instance
rather than reaching into the process environment directly.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Field names map to UPPER_SNAKE env vars (pydantic-settings)."""

    database_url: str = "sqlite:///./data/certgen.db"
    storage_dir: Path = Path("./data/storage")
    dispatcher: Literal["thread", "inline"] = "thread"
    worker_concurrency: int = Field(default=2, ge=1)
    max_recipients_per_job: int = Field(default=1000, ge=1)
    recover_on_startup: bool = True
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor so the environment/`.env` is only parsed once per process."""
    return Settings()
