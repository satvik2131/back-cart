from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    APP_ENV: str = "development"
    APP_NAME: str = "back-cart"

    # Database — async SQLAlchemy URL, e.g.
    # postgresql+asyncpg://user:password@db:5432/back_cart
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/back_cart"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
