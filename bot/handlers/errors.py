"""Global xatoliklarni ushlash."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import ErrorEvent

from bot.i18n import Translator

logger = logging.getLogger(__name__)

router = Router(name="errors")


@router.error()
async def on_error(event: ErrorEvent) -> bool:
    """Xatoni loglaydi va foydalanuvchini xabardor qiladi."""
    exception = event.exception

    if isinstance(exception, TelegramForbiddenError):
        logger.info("Foydalanuvchi botni bloklagan: %s", exception)
        return True
    if isinstance(exception, TelegramRetryAfter):
        logger.warning("Flood limit: %s soniya", exception.retry_after)
        return True

    logger.exception("Ishlov berilmagan xato: %s", exception)

    update = event.update
    translator = Translator("uz")

    try:
        if update.callback_query is not None:
            translator = Translator(update.callback_query.from_user.language_code or "uz")
            await update.callback_query.answer(translator("error.generic"), show_alert=True)
        elif update.message is not None and update.message.chat.type == "private":
            translator = Translator(update.message.from_user.language_code or "uz")
            await update.message.answer(translator("error.generic"))
    except Exception as exc:  # noqa: BLE001 — xato ichidagi xato
        logger.debug("Xato haqida xabar berib bo'lmadi: %s", exc)

    return True
