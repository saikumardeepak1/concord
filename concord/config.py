"""Centralized configuration. All tuning knobs live here, not in code."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelTier(str, Enum):
    FAST = "fast"
    STANDARD = "standard"
    HIGH = "high"


class Settings(BaseSettings):
    """Runtime configuration loaded from environment.

    Single source of truth for model selection, budgets, thresholds, paths.
    Everything an operator might want to tune at deploy time is here.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CONCORD_",
        extra="ignore",
    )

    # Anthropic
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")

    # Model tiering (ADR-003). Keep IDs in config so retiering is a settings change.
    model_fast: str = "claude-haiku-4-5-20251001"
    model_standard: str = "claude-sonnet-4-6"
    model_high: str = "claude-opus-4-7"

    # Budgets
    max_tokens_per_request: int = 8000
    max_turns: int = 8
    max_clarifications: int = 2
    max_tool_retries: int = 2
    request_timeout_seconds: int = 60

    # Escalation thresholds (ADR-010)
    confidence_escalate: float = 0.55
    confidence_auto_action: float = 0.80

    # Storage
    db_url: str = "sqlite+aiosqlite:///./concord.db"
    chroma_path: str = "./.chroma"
    knowledge_dir: str = "./concord/retrieval/knowledge"

    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    env: str = "development"

    # Feature flags
    verification_enabled: bool = True
    pii_redaction_enabled: bool = True

    # Retrieval
    retrieval_top_k: int = 4
    retrieval_chunk_chars: int = 900
    retrieval_chunk_overlap: int = 120
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    def model_for_tier(self, tier: ModelTier) -> str:
        return {
            ModelTier.FAST: self.model_fast,
            ModelTier.STANDARD: self.model_standard,
            ModelTier.HIGH: self.model_high,
        }[tier]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
