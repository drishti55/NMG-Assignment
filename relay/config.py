"""Environment-based configuration. Every field has a safe local default (see .env.example)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    admin_database_url: str = Field(
        "postgresql://postgres:relay@localhost:5432/relay", alias="RELAY_ADMIN_DATABASE_URL"
    )
    database_url: str = Field(
        "postgresql://relay_app:relay_app@localhost:5432/relay", alias="RELAY_DATABASE_URL"
    )
    app_db_password: str = Field("relay_app", alias="RELAY_APP_DB_PASSWORD")
    test_database: str = Field("relay_test", alias="RELAY_TEST_DATABASE")

    ollama_base_url: str = Field("http://localhost:11434", alias="OLLAMA_BASE_URL")
    triage_model: str = Field("qwen2.5:1.5b", alias="RELAY_TRIAGE_MODEL")
    draft_model: str = Field("qwen2.5:3b", alias="RELAY_DRAFT_MODEL")
    embed_model: str = Field("nomic-embed-text", alias="RELAY_EMBED_MODEL")
    embed_dim: int = Field(768, alias="RELAY_EMBED_DIM")
    temperature: float = Field(0.0, alias="RELAY_MODEL_TEMPERATURE")
    seed: int = Field(7, alias="RELAY_MODEL_SEED")
    model_timeout_s: float = Field(120.0, alias="RELAY_MODEL_TIMEOUT_S")

    max_tokens_per_ticket: int = Field(6000, alias="RELAY_MAX_TOKENS_PER_TICKET")
    max_tokens_per_run: int = Field(200_000, alias="RELAY_MAX_TOKENS_PER_RUN")

    retrieval_top_k: int = Field(3, alias="RELAY_RETRIEVAL_TOP_K")
    retrieval_min_score: float = Field(0.5, alias="RELAY_RETRIEVAL_MIN_SCORE")

    lease_seconds: int = Field(30, alias="RELAY_LEASE_SECONDS")
    max_attempts: int = Field(3, alias="RELAY_MAX_ATTEMPTS")
    backoff_base_s: float = Field(2.0, alias="RELAY_BACKOFF_BASE_S")
    backoff_max_s: float = Field(60.0, alias="RELAY_BACKOFF_MAX_S")

    chaos_fail_rate: float = Field(0.0, alias="CHAOS_FAIL_RATE")

    api_port: int = Field(8077, alias="RELAY_API_PORT")
    api_tokens: str = Field("dev-operator-token=operator@northwind|harbor", alias="RELAY_API_TOKENS")

    out_dir: Path = Field(ROOT / "out", alias="RELAY_OUT_DIR")
    eval_dir: Path = Field(ROOT / "eval" / "runs", alias="RELAY_EVAL_DIR")


@lru_cache
def get_settings() -> Settings:
    return Settings()
