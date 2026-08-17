"""Asosiy menyular va umumiy klaviaturalar."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import settings
from bot.i18n import LANGUAGE_NAMES, SUPPORTED_LANGUAGES, Translator
from bot.keyboards.callbacks import (
    CurrencyCB,
    LangCB,
    MenuCB,
    SettingsCB,
    WalletCB,
)
from bot.utils.money import CURRENCIES


def language_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGUAGES:
        builder.button(text=LANGUAGE_NAMES[code], callback_data=LangCB(code=code))
    builder.adjust(1)
    return builder.as_markup()


def main_menu(_: Translator, *, is_admin: bool = False) -> InlineKeyboardMarkup:
    from aiogram.types import WebAppInfo

    builder = InlineKeyboardBuilder()
    if settings.webapp_base_url and settings.webapp_base_url.startswith("https://"):
        builder.button(
            text="📱 Mini App Dashboard",
            web_app=WebAppInfo(url=f"{settings.webapp_base_url.rstrip('/')}/app"),
        )
    builder.button(text=_("menu.wallet"), callback_data=MenuCB(action="wallet"))
    builder.button(text=_("menu.inbox"), callback_data=MenuCB(action="inbox"))
    builder.button(text=_("menu.link"), callback_data=MenuCB(action="link"))
    builder.button(text=_("menu.groups"), callback_data=MenuCB(action="groups"))
    builder.button(text=_("menu.withdraw"), callback_data=MenuCB(action="withdraw"))
    builder.button(text=_("menu.settings"), callback_data=MenuCB(action="settings"))
    builder.button(text=_("menu.help"), callback_data=MenuCB(action="help"))
    if is_admin:
        builder.button(text=_("menu.admin"), callback_data=MenuCB(action="admin"))
    builder.adjust(1, 2, 2, 2, 1)
    return builder.as_markup()


def back_to(_: Translator, action: str = "home") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=_("common.back"), callback_data=MenuCB(action=action))
    return builder.as_markup()


def cancel_keyboard(_: Translator, action: str = "home") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=_("common.cancel"), callback_data=MenuCB(action=action))
    return builder.as_markup()


def settings_menu(_: Translator, *, language: str, currency: str, tz: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("settings.language", current=LANGUAGE_NAMES.get(language, language)),
        callback_data=SettingsCB(action="lang"),
    )
    builder.button(
        text=_("settings.currency", current=_(f"currency.{currency}")),
        callback_data=SettingsCB(action="currency"),
    )
    builder.button(
        text=_("settings.timezone", current=_format_tz(tz)),
        callback_data=SettingsCB(action="tz"),
    )
    builder.button(text=_("settings.referral"), callback_data=SettingsCB(action="referral"))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="home"))
    builder.adjust(1)
    return builder.as_markup()


def currency_keyboard(_: Translator, scope: str = "user") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in CURRENCIES:
        builder.button(text=_(f"currency.{code}"), callback_data=CurrencyCB(code=code, scope=scope))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="settings"))
    builder.adjust(1)
    return builder.as_markup()


#: Ommabop vaqt mintaqalari (daqiqada)
TIMEZONES = (
    ("UTC+5 Toshkent", 300),
    ("UTC+3 Moskva", 180),
    ("UTC+4 Boku", 240),
    ("UTC+6 Astana", 360),
    ("UTC+0 London", 0),
    ("UTC-5 New York", -300),
)


def timezone_keyboard(_: Translator) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, offset in TIMEZONES:
        builder.button(text=label, callback_data=SettingsCB(action="set_tz", value=str(offset)))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="settings"))
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def _format_tz(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    hours, mins = divmod(minutes, 60)
    return f"{sign}{hours}" + (f":{mins:02d}" if mins else "")


def share_link_keyboard(_: Translator, link: str) -> InlineKeyboardMarkup:
    from urllib.parse import quote

    share_url = f"https://t.me/share/url?url={quote(link)}&text={quote(_('link.share_text'))}"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_("link.share"), url=share_url))
    builder.row(InlineKeyboardButton(text=_("common.back"), callback_data=MenuCB(action="home").pack()))
    return builder.as_markup()


def topup_prompt_keyboard(_: Translator) -> InlineKeyboardMarkup:
    """Balans yetmaganda ko'rsatiladigan tugma."""
    builder = InlineKeyboardBuilder()
    builder.button(text=_("relay.topup_now"), callback_data=WalletCB(action="topup"))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="home"))
    builder.adjust(1)
    return builder.as_markup()


def group_topup_keyboard(_: Translator) -> InlineKeyboardMarkup:
    """Guruhdan botga olib boradigan tugma (guruhda inline callback ishlamaydi)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_("group.topup_btn"),
            url=f"https://t.me/{settings.bot_username}?start=topup",
        )
    )
    return builder.as_markup()


def add_to_group_keyboard(_: Translator) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_("group.add_btn"),
            url=f"https://t.me/{settings.bot_username}?startgroup=setup&admin=delete_messages",
        )
    )
    builder.row(
        InlineKeyboardButton(text=_("common.back"), callback_data=MenuCB(action="home").pack())
    )
    return builder.as_markup()
