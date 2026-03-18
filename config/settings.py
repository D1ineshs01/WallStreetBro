"""
Central configuration module.
All modules import `settings` from here — never read env vars directly.
"""

import json
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── xAI / Grok ────────────────────────────────────────────────────
    xai_api_key: str = Field(..., description="xAI API key for Grok access")
    xai_base_url: str = Field("https://api.x.ai/v1", description="xAI API base URL")
    grok_model: str = Field("grok-4-1-fast-non-reasoning", description="Grok model ID")
    grok_rate_limit_rpm: int = Field(607, description="Grok requests per minute limit")
    grok_rate_limit_tpm: int = Field(4_000_000, description="Grok tokens per minute limit")

    # ── Anthropic / Claude ────────────────────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    supervisor_model: str = Field("claude-sonnet-4-5", description="Supervisor node model")
    execution_model: str = Field("claude-opus-4-6", description="Execution agent model")
    visualization_model: str = Field("claude-sonnet-4-5", description="Visualization node model")

    # ── Redis ─────────────────────────────────────────────────────────
    redis_url: str = Field("redis://localhost:6379", description="Redis connection URL")
    redis_password: Optional[str] = Field(None, description="Redis password (optional)")

    # ── PostgreSQL ────────────────────────────────────────────────────
    database_url: str = Field(
        "postgresql+asyncpg://wallstreetbro:wallstreetbro@localhost:5432/wallstreetbro",
        description="Async PostgreSQL connection URL",
    )

    # ── Alpaca ────────────────────────────────────────────────────────
    alpaca_api_key: str = Field(..., description="Alpaca API key")
    alpaca_secret_key: str = Field(..., description="Alpaca secret key")
    alpaca_base_url: str = Field(
        "https://paper-api.alpaca.markets",
        description="Alpaca base URL (paper or live)",
    )

    # ── Risk Management ───────────────────────────────────────────────
    max_drawdown_pct: float = Field(0.05, description="Max portfolio drawdown before kill switch")
    max_position_size_usd: float = Field(10_000.0, description="Max single position size in USD")
    rate_limit_buffer_pct: float = Field(0.80, description="Fraction of rate limit before alerting")

    # ── Grok Scan Config ──────────────────────────────────────────────
    allowed_x_handles: List[str] = Field(
        default_factory=list,
        description="Curated X handles for Grok scanning (max 10)",
    )
    excluded_x_handles: List[str] = Field(
        default_factory=list,
        description="X handles to exclude from scanning",
    )
    enable_image_understanding: bool = Field(
        False, description="Allow Grok to parse images from X posts (triggers view_image cost)"
    )

    # ── Google Sheets (optional) ──────────────────────────────────────
    google_sheets_credentials_json: Optional[str] = Field(None)
    google_sheets_spreadsheet_id: Optional[str] = Field(None)

    @field_validator("allowed_x_handles", mode="before")
    @classmethod
    def parse_x_handles(cls, v):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                v = [h.strip() for h in v.split(",") if h.strip()]
        if len(v) > 10:
            raise ValueError("allowed_x_handles supports a maximum of 10 handles")
        return v

    @field_validator("excluded_x_handles", mode="before")
    @classmethod
    def parse_excluded_handles(cls, v):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                v = [h.strip() for h in v.split(",") if h.strip()]
        return v

    @property
    def is_paper_trading(self) -> bool:
        return "paper-api" in self.alpaca_base_url

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# Module-level singleton — import this throughout the codebase
settings = Settings()
