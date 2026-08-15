"""Fon vazifalari: escrow'ni yakunlash, sessiyalarni yopish, tozalash."""

from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.db.session import get_sessionmaker
from bot.services import relay as relay_service

logger = logging.getLogger(__name__)


async def settle_escrow(bot: Bot) -> None:
    """Muddati o'tgan kafolatlarni yakunlaydi."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            released, refunded = await relay_service.expire_holds(bot, session)
            if released or refunded:
                await session.commit()
                logger.info("Escrow yakunlandi: %s o'tkazildi, %s qaytarildi", released, refunded)
        except Exception:
            await session.rollback()
            logger.exception("Escrow vazifasida xato")


async def close_sessions() -> None:
    """Muddati tugagan to'langan suhbat sessiyalarini yopadi."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            closed = await relay_service.close_expired_sessions(session)
            if closed:
                await session.commit()
                logger.info("%s ta sessiya yopildi", closed)
        except Exception:
            await session.rollback()
            logger.exception("Sessiyalarni yopishda xato")


async def cleanup_usage(days: int = 60) -> None:
    """Eski kunlik hisoblagichlarni o'chiradi (baza shishmasligi uchun)."""
    from datetime import timedelta

    from sqlalchemy import delete

    from bot.db.models import ChatUsage, InboxUsage
    from bot.utils.timeutils import local_day_key, utcnow

    cutoff = local_day_key(utcnow() - timedelta(days=days), 0)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            await session.execute(delete(ChatUsage).where(ChatUsage.day < cutoff))
            await session.execute(delete(InboxUsage).where(InboxUsage.day < cutoff))
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Tozalash vazifasida xato")


def create_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        settle_escrow, "interval", minutes=5, args=(bot,), id="settle_escrow",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        close_sessions, "interval", minutes=10, id="close_sessions",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(cleanup_usage, "cron", hour=3, minute=30, id="cleanup_usage")
    return scheduler
