"""Har bir hodisa uchun bitta DB sessiyasi ochadi va tranzaksiyani yopadi."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.db.session import get_sessionmaker

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise
            else:
                # Handler ichida commit qilinmagan bo'lsa — shu yerda yakunlanadi
                if session.in_transaction():
                    await session.commit()
                return result
