"""Foydalanuvchini bazadan oladi, tarjimonni va hamyonni kontekstga qo'yadi."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.i18n import Translator
from bot.services import app_settings, users
from bot.utils.money import format_amount

logger = logging.getLogger(__name__)

#: Texnik ish rejimida ham ishlaydigan buyruqlar
MAINTENANCE_ALLOWED = ("/start", "/admin")


class UserContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        tg_user = data.get("event_from_user")

        if session is None or tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        # Referal havolasidan kelgan bo'lsa
        referrer_id = _extract_referrer(event)
        user = await users.get_or_create(session, tg_user, referrer_id=referrer_id)

        translator = Translator(user.language)
        rate_uzs, rate_usd = await app_settings.rates(session)

        data["user"] = user
        data["_"] = translator
        data["lang"] = user.language
        data["rates"] = (rate_uzs, rate_usd)
        data["fmt"] = _make_formatter(user.display_currency, rate_uzs, rate_usd)

        # Bloklangan foydalanuvchi
        if user.is_banned:
            text = translator("error.banned", reason=user.ban_reason or "—")
            await _reply(event, text)
            return None

        # Texnik ish rejimi (adminlar uchun ochiq)
        if not user.is_admin and await app_settings.get(session, "maintenance", False):
            if not _is_allowed_in_maintenance(event):
                text = translator(
                    "error.maintenance",
                    text=await app_settings.get(session, "maintenance_text", ""),
                )
                await _reply(event, text)
                return None

        return await handler(event, data)


def _make_formatter(currency: str, rate_uzs: float, rate_usd: float):
    """Kontekstga qo'yiladigan qulay formatlovchi: `fmt(mxtr)`."""

    def formatter(amount_mxtr: int, currency_override: str | None = None) -> str:
        return format_amount(
            amount_mxtr,
            currency_override or currency,
            rate_uzs=rate_uzs,
            rate_usd=rate_usd,
        )

    return formatter


def _extract_referrer(event: TelegramObject) -> int | None:
    if not isinstance(event, Message) or not event.text:
        return None
    if not event.text.startswith("/start "):
        return None
    payload = event.text.split(maxsplit=1)[1].strip()
    if payload.startswith("r_") and payload[2:].isdigit():
        return int(payload[2:])
    return None


def _is_allowed_in_maintenance(event: TelegramObject) -> bool:
    if isinstance(event, Message) and event.text:
        return event.text.split()[0] in MAINTENANCE_ALLOWED
    return False


async def _reply(event: TelegramObject, text: str) -> None:
    try:
        if isinstance(event, Message):
            if event.chat.type == "private":
                await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer(text[:200], show_alert=True)
    except Exception as exc:  # xabar yetkazilmasa — jim o'tamiz
        logger.debug("Ogohlantirish yuborilmadi: %s", exc)
