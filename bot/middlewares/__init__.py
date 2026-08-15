"""Middleware'lar: DB sessiyasi, foydalanuvchi, til, throttling."""

from aiogram import Dispatcher

from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.middlewares.user_context import UserContextMiddleware


def setup(dp: Dispatcher) -> None:
    """Middleware'larni to'g'ri tartibda ulaydi."""
    for observer in (dp.message, dp.callback_query, dp.pre_checkout_query, dp.inline_query):
        observer.middleware(DatabaseMiddleware())
        observer.middleware(UserContextMiddleware())

    # Throttling faqat foydalanuvchi tashabbusidagi hodisalarga
    dp.message.middleware(ThrottlingMiddleware(rate_limit=0.4))
    dp.callback_query.middleware(ThrottlingMiddleware(rate_limit=0.25))

    # Chat a'zoligi hodisalari uchun ham DB kerak
    dp.my_chat_member.middleware(DatabaseMiddleware())
    dp.chat_member.middleware(DatabaseMiddleware())


__all__ = ["setup", "DatabaseMiddleware", "UserContextMiddleware", "ThrottlingMiddleware"]
