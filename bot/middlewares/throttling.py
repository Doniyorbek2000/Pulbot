"""Oddiy anti-flood: bir foydalanuvchidan juda tez kelgan hodisalarni tashlaydi."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

MAX_TRACKED_USERS = 20_000


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit: float = 0.4) -> None:
        self.rate_limit = rate_limit
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        # Guruhdagi xabarlar hisob-kitobga ta'sir qiladi — ularni tashlab bo'lmaydi
        if isinstance(event, Message) and event.chat.type in ("group", "supergroup", "channel"):
            return await handler(event, data)

        now = time.monotonic()
        last = self._last.get(tg_user.id, 0.0)
        if now - last < self.rate_limit:
            if isinstance(event, CallbackQuery):
                await event.answer()
            return None

        if len(self._last) > MAX_TRACKED_USERS:
            self._last.clear()
        self._last[tg_user.id] = now
        return await handler(event, data)
