"""Foydalanuvchi sozlamalari: til, valyuta, vaqt mintaqasi, referal."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.handlers.common import format_tz, safe_edit
from bot.i18n import Translator
from bot.keyboards.callbacks import CurrencyCB, MenuCB, SettingsCB
from bot.keyboards.menus import (
    back_to,
    currency_keyboard,
    language_keyboard,
    settings_menu,
    timezone_keyboard,
)
from bot.services import users

logger = logging.getLogger(__name__)

router = Router(name="settings")


@router.callback_query(MenuCB.filter(F.action == "settings"))
@router.callback_query(SettingsCB.filter(F.action == "home"))
async def open_settings(
    query: CallbackQuery, state: FSMContext, user: User, _: Translator
) -> None:
    await state.clear()
    await _render(query, user, _)
    await query.answer()


async def _render(query: CallbackQuery, user: User, _: Translator) -> None:
    await safe_edit(
        query,
        _("settings.title"),
        settings_menu(
            _,
            language=user.language,
            currency=user.display_currency,
            tz=user.tz_offset_minutes,
        ),
    )


@router.callback_query(SettingsCB.filter(F.action == "lang"))
async def choose_language(query: CallbackQuery, _: Translator) -> None:
    await safe_edit(query, _("language.choose"), language_keyboard())
    await query.answer()


@router.callback_query(SettingsCB.filter(F.action == "currency"))
async def choose_currency(query: CallbackQuery, _: Translator) -> None:
    await safe_edit(query, _("currency.choose"), currency_keyboard(_, "user"))
    await query.answer()


@router.callback_query(CurrencyCB.filter(F.scope == "user"))
async def set_currency(
    query: CallbackQuery,
    callback_data: CurrencyCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    user.display_currency = callback_data.code
    await session.flush()
    await query.answer(_("currency.changed", currency=_(f"currency.{callback_data.code}")))
    await _render(query, user, _)


@router.callback_query(SettingsCB.filter(F.action == "tz"))
async def choose_timezone(query: CallbackQuery, _: Translator) -> None:
    await safe_edit(query, _("settings.timezone_prompt"), timezone_keyboard(_))
    await query.answer()


@router.callback_query(SettingsCB.filter(F.action == "set_tz"))
async def set_timezone(
    query: CallbackQuery,
    callback_data: SettingsCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    try:
        offset = int(callback_data.value)
    except ValueError:
        await query.answer()
        return

    user.tz_offset_minutes = max(-12 * 60, min(14 * 60, offset))
    await session.flush()
    await query.answer(_("settings.timezone_saved", tz=format_tz(user.tz_offset_minutes)))
    await _render(query, user, _)


@router.callback_query(SettingsCB.filter(F.action == "referral"))
async def show_referral(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator
) -> None:
    count = await users.count_referrals(session, user.id)
    await safe_edit(
        query,
        _("settings.referral_text", link=users.referral_link(user.id), count=count),
        back_to(_, "settings"),
    )
    await query.answer()
