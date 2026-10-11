"""Runtime settings, read from the environment (and .env when present)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_prefix="", extra="ignore")

    groq_api_key: str = ""
    quotient_model: str = "qwen/qwen3.8-27b"
    quotient_reasoning_effort: str = "none"
    quotient_data_dir: Path = ROOT / "data"
    quotient_rules_file: Path = ROOT / "quotient" / "pricing" / "rules.yaml"
    quotient_cors_origins: str = "http://localhost:3000"

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"


@lru_cache
def settings() -> Settings:
    return Settings()
