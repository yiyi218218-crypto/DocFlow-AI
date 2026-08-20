"""Application settings.

One Settings class, validated at import. Never call os.getenv() outside this module —
that is how configuration drifts and how secrets end up hard-coded in a handler.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- environment ------------------------------------------------------
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"
    version: str = "0.1.0"

    # --- security ---------------------------------------------------------
    secret_key: SecretStr = SecretStr(secrets.token_urlsafe(64))
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 4

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- database ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://docflow:docflow@localhost:5432/docflow"
    database_admin_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/docflow"
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False

    # --- redis ------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- object storage ---------------------------------------------------
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_region: str = "auto"
    s3_access_key_id: str = "docflow"
    s3_secret_access_key: SecretStr = SecretStr("docflow123")
    s3_bucket: str = "docflow"
    s3_public_base_url: str | None = None
    presign_ttl_seconds: int = 300
    presign_image_ttl_seconds: int = 900

    # --- llm --------------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None

    llm_tier_fast: str = "anthropic:claude-haiku-4-5"
    llm_tier_balanced: str = "anthropic:claude-sonnet-5"
    llm_tier_deep: str = "anthropic:claude-opus-5"
    embedding_model: str = "openai:text-embedding-3-small"
    embedding_dimensions: int = 1536

    llm_timeout_seconds: int = 120
    llm_max_retries: int = 3

    # --- cost controls ----------------------------------------------------
    max_cost_per_document_usd: float = 2.00
    daily_budget_per_workspace_usd: float = 5.00
    free_tier_documents_per_month: int = 10
    free_tier_queries_per_month: int = 30

    # --- ingestion limits -------------------------------------------------
    max_upload_bytes: int = 25 * 1024 * 1024
    max_page_count: int = 150
    allowed_mime_types: list[str] = Field(
        default_factory=lambda: [
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/webp",
            "text/plain",
            "text/markdown",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ]
    )

    # --- pipeline ---------------------------------------------------------
    extraction_window_pages: int = 4
    extraction_window_overlap: int = 1
    full_context_token_threshold: int = 30_000
    chunk_target_tokens: int = 700
    chunk_overlap_ratio: float = 0.12
    ocr_render_dpi: int = 300
    page_render_dpi: int = 150

    # Citation alignment thresholds. docs/10-architecture.md §11.5
    citation_verified_threshold: float = 95.0
    citation_partial_threshold: float = 88.0

    # --- retrieval --------------------------------------------------------
    retrieval_candidates: int = 50
    retrieval_final_k: int = 8
    rrf_k: int = 60
    hnsw_ef_search: int = 100
    similarity_floor: float = 0.25

    # --- demo -------------------------------------------------------------
    demo_mode: bool = False
    demo_workspace_slug: str = "demo"

    # --- observability ----------------------------------------------------
    sentry_dsn: str | None = None
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("cors_origins", "allowed_mime_types", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str) and not v.startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.environment == "production":
            weak = self.secret_key.get_secret_value()
            if len(weak) < 32 or "change-me" in weak or "dev-only" in weak:
                raise ValueError("SECRET_KEY must be a strong unique value in production")
            if self.debug:
                raise ValueError("DEBUG must be false in production")
        return self

    # --- derived ----------------------------------------------------------
    @property
    def is_local(self) -> bool:
        return self.environment in ("local", "test")

    @property
    def has_any_llm_key(self) -> bool:
        return any([self.anthropic_api_key, self.openai_api_key, self.gemini_api_key])

    @property
    def effective_demo_mode(self) -> bool:
        """Demo mode is forced on when no provider key is configured.

        Better to serve fixtures than to fail every upload with an auth error.
        """
        return self.demo_mode or not self.has_any_llm_key

    def sync_database_url(self, admin: bool = False) -> str:
        url = self.database_admin_url if admin else self.database_url
        return url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
