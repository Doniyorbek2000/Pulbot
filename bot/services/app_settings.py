"""Ish vaqtida o'zgartiriladigan global sozlamalar (kurs, komissiya, limitlar).

Qiymatlar `app_settings` jadvalida saqlanadi va xotirada keshlanadi, shuning
uchun har bir xabar uchun DB ga murojaat qilinmaydi.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import AppSetting

CACHE_TTL_SECONDS = 30

DEFAULTS: dict[str, Any] = {
    "commission_bps": settings.commission_bps,
    "rate_uzs_per_star": settings.rate_uzs_per_star,
    "rate_usd_per_star": settings.rate_usd_per_star,
    "min_topup_stars": settings.min_topup_stars,
    "min_price_stars": settings.min_price_stars,
    "max_price_stars": settings.max_price_stars,
    "min_withdraw_stars": settings.min_withdraw_stars,
    "withdraw_fee_bps": settings.withdraw_fee_bps,
    "withdraw_hold_hours": settings.withdraw_hold_hours,
    "withdraw_daily_limit_stars": 500_000,
    "maintenance": False,
    "maintenance_text": "",
    "referral_bonus_stars": 0,
    "topup_presets_stars": [50, 100, 250, 500, 1000, 2500],
    "withdraw_enabled": True,
    "support_username": "",
    "terms_url": "",
}

_cache: dict[str, Any] = {}
_cache_at: float = 0.0


def _expired() -> bool:
    return (time.monotonic() - _cache_at) > CACHE_TTL_SECONDS


async def load(session: AsyncSession, *, force: bool = False) -> dict[str, Any]:
    """Barcha sozlamalarni keshdan yoki DB dan qaytaradi."""
    global _cache, _cache_at
    if _cache and not _expired() and not force:
        return _cache
    rows = (await session.execute(select(AppSetting))).scalars().all()
    values = dict(DEFAULTS)
    for row in rows:
        # JSON ustunida qiymat {"v": ...} ko'rinishida saqlanadi
        if isinstance(row.value, dict) and "v" in row.value:
            values[row.key] = row.value["v"]
    _cache = values
    _cache_at = time.monotonic()
    return values


async def get(session: AsyncSession, key: str, default: Any = None) -> Any:
    values = await load(session)
    return values.get(key, DEFAULTS.get(key, default))


async def set_value(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value={"v": value}))
    else:
        row.value = {"v": value}
    await session.flush()
    invalidate()


def invalidate() -> None:
    global _cache_at
    _cache_at = 0.0


async def rates(session: AsyncSession) -> tuple[float, float]:
    """(so'm/yulduzcha, dollar/yulduzcha)."""
    values = await load(session)
    return float(values["rate_uzs_per_star"]), float(values["rate_usd_per_star"])


async def commission_bps(session: AsyncSession) -> int:
    return int(await get(session, "commission_bps", settings.commission_bps))
