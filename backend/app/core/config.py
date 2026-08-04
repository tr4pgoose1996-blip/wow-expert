"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings.

    All values are sourced from environment variables (or a local .env file).
    No secret has a usable default: production deployments must supply them.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -----------------------------------------------------
    PROJECT_NAME: str = "wow! expert."
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["local", "test", "staging", "production"] = "local"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    # --- Security --------------------------------------------------------
    SECRET_KEY: str = Field(min_length=32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    JWT_ALGORITHM: str = "HS256"
    PASSWORD_MIN_LENGTH: int = 12

    # --- CORS ------------------------------------------------------------
    # NoDecode stops pydantic-settings from JSON-parsing the raw env value
    # before the validator below runs, which is what lets the documented
    # comma-separated form work.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            # Accept a JSON array as well, so both documented styles work.
            text = value.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [origin.strip() for origin in text.split(",") if origin.strip()]
        return value

    # --- Database --------------------------------------------------------
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "wowexpert"
    POSTGRES_PASSWORD: str = "wowexpert"
    POSTGRES_DB: str = "wowexpert"
    DATABASE_URL: PostgresDsn | None = None
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    # --- Redis -----------------------------------------------------------
    REDIS_URL: RedisDsn = Field(default="redis://localhost:6379/0")
    CACHE_DEFAULT_TTL_SECONDS: int = 300

    # --- Rate limiting ---------------------------------------------------
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 120
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # --- Blizzard Battle.net ----------------------------------------------
    BLIZZARD_CLIENT_ID: str | None = None
    BLIZZARD_CLIENT_SECRET: str | None = None
    BLIZZARD_REGION: Literal["us", "eu", "kr", "tw", "cn"] = "us"
    BLIZZARD_LOCALE: str = "en_US"
    BLIZZARD_REDIRECT_URI: str = "http://localhost:8000/api/v1/blizzard/oauth/callback"
    # NoDecode for the same reason as CORS_ORIGINS: the validator below
    # accepts the space-separated form OAuth actually uses.
    BLIZZARD_SCOPES: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["openid", "wow.profile"]
    )
    BLIZZARD_TIMEOUT_SECONDS: int = 20
    BLIZZARD_MAX_RETRIES: int = 3
    # Blizzard permits 100 req/s and 36,000 req/hour per client.
    BLIZZARD_MAX_CONCURRENCY: int = 20
    BLIZZARD_STATIC_CACHE_TTL: int = 86_400
    BLIZZARD_PROFILE_CACHE_TTL: int = 900
    # Fernet key protecting stored Battle.net refresh tokens at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet;
    # print(Fernet.generate_key().decode())"
    BLIZZARD_TOKEN_ENCRYPTION_KEY: str | None = None

    # --- Synchronization ---------------------------------------------------
    SYNC_ENABLED: bool = True
    SYNC_INTERVAL_SECONDS: int = 3600
    SYNC_STALE_AFTER_HOURS: int = 168  # weekly
    SYNC_BATCH_SIZE: int = 25

    @field_validator("BLIZZARD_SCOPES", mode="before")
    @classmethod
    def _split_scopes(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [s.strip() for s in value.replace(",", " ").split() if s.strip()]
        return value

    @property
    def blizzard_configured(self) -> bool:
        return bool(self.BLIZZARD_CLIENT_ID and self.BLIZZARD_CLIENT_SECRET)

    @property
    def blizzard_api_host(self) -> str:
        if self.BLIZZARD_REGION == "cn":
            return "https://gateway.battlenet.com.cn"
        return f"https://{self.BLIZZARD_REGION}.api.blizzard.com"

    @property
    def blizzard_oauth_host(self) -> str:
        if self.BLIZZARD_REGION == "cn":
            return "https://www.battlenet.com.cn/oauth"
        return "https://oauth.battle.net"

    # --- AI provider -------------------------------------------------------
    AI_PROVIDER: Literal["openai", "null"] = "null"
    OPENAI_API_KEY: str | None = None
    AI_MODEL: str = "gpt-4o-mini"
    AI_TIMEOUT_SECONDS: int = 30

    # --- Knowledge engine --------------------------------------------------
    # "hashing" is a deterministic offline embedder: it makes the whole
    # retrieval stack testable in CI without network access or API spend, but
    # it is lexical only. Use "openai" for production semantic search.
    EMBEDDING_PROVIDER: Literal["openai", "hashing"] = "hashing"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    # Must match the pgvector column width. Changing this requires a
    # migration and a full re-embed; the stored embedding_model column makes
    # the mismatch detectable.
    EMBEDDING_DIMENSIONS: int = 1536
    EMBEDDING_TIMEOUT_SECONDS: int = 60

    KNOWLEDGE_CHUNK_TARGET_TOKENS: int = 400
    KNOWLEDGE_CHUNK_OVERLAP_TOKENS: int = 60
    KNOWLEDGE_DEFAULT_TOP_K: int = 8
    # Set true only with written permission from sources whose terms restrict
    # commercial use (notably Raider.IO).
    KNOWLEDGE_COMMERCIAL_MODE: bool = False
    # Weekly full re-crawl by default; Blizzard static data changes per patch.
    KNOWLEDGE_REFRESH_INTERVAL_HOURS: int = 168

    @property
    def sqlalchemy_dsn(self) -> str:
        """Async SQLAlchemy DSN, derived from parts when DATABASE_URL is unset."""
        if self.DATABASE_URL is not None:
            dsn = str(self.DATABASE_URL)
            if dsn.startswith("postgresql://"):
                dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
            return dsn
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def alembic_dsn(self) -> str:
        """Synchronous DSN used by Alembic migrations."""
        return self.sqlalchemy_dsn.replace("+asyncpg", "+psycopg")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
