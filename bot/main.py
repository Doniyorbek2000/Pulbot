"""Botni ishga tushirish."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from bot import handlers, middlewares
from bot.config import settings
from bot.db.session import close_db, init_db
from bot.i18n import SUPPORTED_LANGUAGES, get, load_locales
from bot.scheduler import create_scheduler

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def build_storage():
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        logger.info("FSM storage: Redis")
        return RedisStorage.from_url(settings.redis_url)
    logger.info("FSM storage: xotira (bitta instans uchun)")
    return MemoryStorage()


async def set_commands(bot: Bot) -> None:
    """Har bir til uchun buyruqlar menyusi."""
    private_commands = [
        ("start", "cmdlist.start"),
        ("balans", "cmdlist.balance"),
        ("havola", "cmdlist.link"),
        ("narx", "cmdlist.price"),
        ("yechish", "cmdlist.withdraw"),
        ("til", "cmdlist.language"),
        ("yordam", "cmdlist.help"),
    ]
    group_commands = [
        ("sozlash", "cmdlist.setup"),
        ("narx", "cmdlist.group_price"),
        ("bepul", "cmdlist.group_free"),
        ("bloklash", "cmdlist.group_block"),
    ]

    for lang in SUPPORTED_LANGUAGES:
        await bot.set_my_commands(
            [BotCommand(command=name, description=get(key, lang)) for name, key in private_commands],
            scope=BotCommandScopeAllPrivateChats(),
            language_code=lang,
        )
        await bot.set_my_commands(
            [BotCommand(command=name, description=get(key, lang)) for name, key in group_commands],
            scope=BotCommandScopeAllGroupChats(),
            language_code=lang,
        )


async def on_startup(bot: Bot) -> None:
    await init_db()
    me = await bot.get_me()
    if not settings.bot_username:
        settings.bot_username = me.username or ""
    logger.info("Bot ishga tushdi: @%s (id=%s)", me.username, me.id)
    try:
        await set_commands(bot)
    except Exception:
        logger.exception("Buyruqlar menyusini o'rnatib bo'lmadi")


async def main() -> None:
    setup_logging()
    load_locales()

    if not settings.bot_token:
        logger.error("BOT_TOKEN ko'rsatilmagan. .env faylini tekshiring.")
        raise SystemExit(1)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = Dispatcher(storage=build_storage())

    middlewares.setup(dp)
    handlers.setup(dp)

    scheduler = create_scheduler(bot)

    await on_startup(bot)
    scheduler.start()

    try:
        if settings.use_webhook:
            await run_webhook(bot, dp)
        else:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await close_db()
        await bot.session.close()


async def run_webhook(bot: Bot, dp: Dispatcher) -> None:
    """Webhook rejimi (yuqori yuklama uchun)."""
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    await bot.set_webhook(
        url=settings.webhook_url.rstrip("/") + settings.webhook_path,
        secret_token=settings.webhook_secret or None,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )

    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=settings.webhook_secret or None
    ).register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webapp_host, settings.webapp_port)
    await site.start()
    logger.info("Webhook tinglanmoqda: %s:%s", settings.webapp_host, settings.webapp_port)

    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi")
