"""Настройки из окружения. Ни одного значения по умолчанию для секретов."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    database_url: str = Field(alias="DATABASE_URL")
    #: Подпись access-токенов. Смена ключа обесценивает все выданные — это и есть
    #: способ разом всех выкинуть.
    jwt_secret: str = Field(alias="JWT_SECRET", min_length=32)
    jwt_algorithm: str = "HS256"

    access_ttl_minutes: int = 15
    refresh_ttl_days: int = 30

    version: str = Field(default="1.0.0", alias="APP_VERSION")

    #: Пул: воркеров uvicorn немного, а соединений к базе тем более.
    pool_size: int = 5
    max_overflow: int = 5


@lru_cache
def settings() -> Settings:
    return Settings()          # type: ignore[call-arg]
