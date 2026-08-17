"""Router'larni ro'yxatdan o'tkazish.

Tartib muhim: aniqroq filtrlar oldin, umumiy (catch-all) handler'lar oxirida.
"""

from aiogram import Dispatcher

from bot.handlers import (
    admin,
    business,
    channel_guard,
    errors,
    fallback,
    group_guard,
    groups,
    inbox,
    relay,
    rules,
    schedules,
    settings,
    stars,
    start,
    wallet,
    withdraw,
)


def setup(dp: Dispatcher) -> None:
    dp.include_router(errors.router)
    dp.include_router(stars.router)
    dp.include_router(start.router)
    dp.include_router(settings.router)
    dp.include_router(wallet.router)
    dp.include_router(inbox.router)
    dp.include_router(schedules.router)
    dp.include_router(rules.router)
    dp.include_router(withdraw.router)
    dp.include_router(admin.router)
    dp.include_router(channel_guard.router)
    # Business — Telegram Business xabarlari va ulanishlari
    dp.include_router(business.router)
    # Relay — shaxsiy chatdagi javoblarni ushlaydi
    dp.include_router(relay.router)
    # Guruhdagi barcha xabarlar shu yerda tekshiriladi
    dp.include_router(group_guard.router)
    dp.include_router(groups.router)
    # Eng oxirida — tushunilmagan xabarlar
    dp.include_router(fallback.router)


__all__ = ["setup"]
