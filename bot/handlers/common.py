"""Handler'lar uchun umumiy yordamchilar va ekran render'lari."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import InboxSettings, User
from bot.i18n import Translator
from bot.keyboards.menus import main_menu
from bot.services import app_settings, users, wallet
from bot.utils.money import format_amount, from_currency

logger = logging.getLogger(__name__)


async def safe_edit(
    event: CallbackQuery | Message,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    **kwargs,
) -> None:
    """Xabarni tahrirlaydi; imkoni bo'lmasa yangisini yuboradi."""
    message = event.message if isinstance(event, CallbackQuery) else event
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=keyboard, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        try:
            await message.answer(text, reply_markup=keyboard, **kwargs)
        except TelegramBadRequest:
            logger.debug("Xabarni yangilab bo'lmadi: %s", exc)


async def render_main_menu(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
    *,
    edit: bool = True,
) -> None:
    balance_mxtr, _available = await wallet.balance(session, user.id)
    is_active = bool(user.business_enabled)

    text = _(
        "menu.title",
        balance=fmt(balance_mxtr),
        link=users.deep_link(user.public_code),
    )
    keyboard = main_menu(_, is_admin=user.is_admin, is_active=is_active)
    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, keyboard, disable_web_page_preview=True)
    else:
        message = event.message if isinstance(event, CallbackQuery) else event
        await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


def parse_amount(raw: str) -> Decimal | None:
    """Foydalanuvchi kiritgan summani o'qiydi: '1 000', '1.5', '2,5'.

    Har xil bo'shliq belgilari (oddiy, uzilmas, ingichka) va o'nlik vergul
    hisobga olinadi — ko'chirib qo'yilgan matn ham to'g'ri o'qiladi.
    """
    cleaned = "".join(ch for ch in raw if not ch.isspace()).replace(",", ".")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    if value < 0:
        return None
    return value


async def parse_price_input(
    session: AsyncSession, raw: str, currency: str
) -> int | None:
    """Kiritilgan narxni tanlangan valyutadan mXTR ga o'giradi."""
    value = parse_amount(raw)
    if value is None:
        return None
    rate_uzs, rate_usd = await app_settings.rates(session)
    return from_currency(value, currency, rate_uzs, rate_usd)


async def price_bounds(session: AsyncSession) -> tuple[int, int]:
    """(min, max) narx mXTR da."""
    from bot.utils.money import stars_to_mxtr

    min_stars = int(await app_settings.get(session, "min_price_stars", 1))
    max_stars = int(await app_settings.get(session, "max_price_stars", 100_000))
    return stars_to_mxtr(min_stars), stars_to_mxtr(max_stars)


async def make_fmt(session: AsyncSession, currency: str):
    """Berilgan valyuta uchun formatlovchi."""
    rate_uzs, rate_usd = await app_settings.rates(session)

    def fmt(amount_mxtr: int) -> str:
        return format_amount(amount_mxtr, currency, rate_uzs=rate_uzs, rate_usd=rate_usd)

    return fmt


def price_example(currency: str) -> str:
    return {"UZS": "5000", "USD": "0.5", "XTR": "25"}.get(currency, "100")


def onoff(_: Translator, value: bool) -> str:
    return _("common.enabled") if value else _("common.disabled")


def yesno(_: Translator, value: bool) -> str:
    return "✅" if value else "❌"


def format_tz(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    hours, mins = divmod(minutes, 60)
    return f"{sign}{hours}" + (f":{mins:02d}" if mins else "")


def mode_label(_: Translator, mode: str) -> str:
    return _(f"mode.{mode}")


def unit_label(_: Translator, inbox: InboxSettings) -> str:
    return _(f"unit.{inbox.pricing_unit}", minutes=inbox.session_minutes)


async def notify(bot, user_id: int, text: str, keyboard=None) -> bool:
    """Foydalanuvchiga xabar yuboradi, bloklagan bo'lsa jim o'tadi."""
    from aiogram.exceptions import TelegramAPIError

    try:
        await bot.send_message(user_id, text, reply_markup=keyboard)
        return True
    except TelegramAPIError as exc:
        logger.debug("Bildirishnoma yetkazilmadi (%s): %s", user_id, exc)
        return False
