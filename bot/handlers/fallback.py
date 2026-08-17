"""Hech qaysi handler ushlamagan shaxsiy xabarlar uchun zaxira."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.handlers.common import render_main_menu
from bot.i18n import Translator

router = Router(name="fallback")


@router.message(F.chat.type == "private")
async def unknown_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    """Foydalanuvchini adashtirmaslik uchun asosiy menyuni ko'rsatamiz."""
    if await state.get_state() is not None:
        return
    await render_main_menu(message, session, user, _, fmt, edit=False)
