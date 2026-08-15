"""Ilova konfiguratsiyasi (.env dan o'qiladi)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    bot_username: str = Field(default="", alias="BOT_USERNAME")
    admin_ids: list[int] = Field(default_factory=list, alias="ADMIN_IDS")

    # --- Infratuzilma ---
    database_url: str = Field(default="sqlite+aiosqlite:///pulbot.db", alias="DATABASE_URL")
    redis_url: str = Field(default="", alias="REDIS_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Til ---
    default_language: str = Field(default="uz", alias="DEFAULT_LANGUAGE")

    # --- Moliya (boshlang'ich qiymatlar; admin panelida o'zgartiriladi) ---
    commission_bps: int = Field(default=500, alias="COMMISSION_BPS")
    min_topup_stars: int = Field(default=1, alias="MIN_TOPUP_STARS")
    min_price_stars: int = Field(default=1, alias="MIN_PRICE_STARS")
    max_price_stars: int = Field(default=100_000, alias="MAX_PRICE_STARS")

    min_withdraw_stars: int = Field(default=1000, alias="MIN_WITHDRAW_STARS")
    withdraw_fee_bps: int = Field(default=200, alias="WITHDRAW_FEE_BPS")
    withdraw_hold_hours: int = Field(default=72, alias="WITHDRAW_HOLD_HOURS")

    default_hold_hours: int = Field(default=48, alias="DEFAULT_HOLD_HOURS")

    rate_uzs_per_star: float = Field(default=170.0, alias="RATE_UZS_PER_STAR")
    rate_usd_per_star: float = Field(default=0.013, alias="RATE_USD_PER_STAR")

    # --- Webhook (ixtiyoriy) ---
    webhook_url: str = Field(default="", alias="WEBHOOK_URL")
    webhook_path: str = Field(default="/webhook", alias="WEBHOOK_PATH")
    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")
    webapp_host: str = Field(default="0.0.0.0", alias="WEBAPP_HOST")
    webapp_port: int = Field(default=8080, alias="WEBAPP_PORT")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> list[int]:
        if value in (None, "", []):
            return []
        if isinstance(value, (list, tuple)):
            return [int(v) for v in value]
        return [int(part) for part in str(value).replace(" ", "").split(",") if part]

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
