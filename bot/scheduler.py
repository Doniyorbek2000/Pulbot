"""Fon vazifalari: escrow, obunalarni tekshirish, sessiyalarni yopish, tozalash."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.types import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import TargetType
from bot.db.models import (
    ActivePermission,
    ChatSettings,
    ChatUsage,
    InboxUsage,
    Subscription,
)
from bot.db.session import get_sessionmaker
from bot.services import relay as relay_service
from bot.utils.timeutils import local_day_key, utcnow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Obunalar Muddatini Nazorat Qilish (Subscription Manager)
# ---------------------------------------------------------------------------


async def check_expiring_subscriptions(bot: Bot) -> None:
    """Muddati tugayotgan va tugagan kanal obunalarini tekshiradi."""
    sessionmaker = get_sessionmaker()
    now = utcnow()
    warning_threshold = now + timedelta(days=1)

    async with sessionmaker() as session:
        try:
            # A) Tugashiga 24 soat qolganlarni ogohlantirish
            warn_stmt = select(Subscription).where(
                Subscription.is_active.is_(True),
                Subscription.warned_expiration.is_(False),
                Subscription.expires_at <= warning_threshold,
                Subscription.expires_at > now,
            )
            warn_subs = (await session.execute(warn_stmt)).scalars().all()

            for sub in warn_subs:
                chat_row = (
                    await session.execute(
                        select(ChatSettings).where(ChatSettings.chat_id == sub.chat_id)
                    )
                ).scalar_one_or_none()

                sub.warned_expiration = True
                try:
                    markup = None
                    if settings.bot_username:
                        markup = InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="🔄 Obunani uzaytirish",
                                        url=f"https://t.me/{settings.bot_username}?start=chat_{abs(sub.chat_id)}",
                                    )
                                ]
                            ]
                        )
                    await bot.send_message(
                        chat_id=sub.user_id,
                        text=f"⚠️ <b>Obuna muddati tugamoqda!</b>\n\n"
                        f"Kanal/Guruh: <b>{chat_row.title if chat_row else sub.chat_id}</b>\n"
                        f"Obunangiz <b>24 soat ichida</b> tugaydi. A'zolikni saqlab qolish uchun uni uzaytirishingiz mumkin.",
                        reply_markup=markup,
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.debug("Foydalanuvchiga obuna ogohlantirishi yetmadi (%s): %s", sub.user_id, e)

            # B) Muddati to'liq tugagan obunalarni kanaldan chiqarish
            expired_stmt = select(Subscription).where(
                Subscription.is_active.is_(True),
                Subscription.expires_at <= now,
            )
            expired_subs = (await session.execute(expired_stmt)).scalars().all()

            for sub in expired_subs:
                sub.is_active = False
                chat_row = (
                    await session.execute(
                        select(ChatSettings).where(ChatSettings.chat_id == sub.chat_id)
                    )
                ).scalar_one_or_none()

                # Kanaldan chiqarish: ban + unban
                try:
                    await bot.ban_chat_member(chat_id=sub.chat_id, user_id=sub.user_id)
                    await bot.unban_chat_member(chat_id=sub.chat_id, user_id=sub.user_id)
                except Exception as e:
                    logger.warning("Foydalanuvchini kanaldan chiqarishda xato (%s): %s", sub.user_id, e)

                # Foydalanuvchiga xabar berish
                try:
                    await bot.send_message(
                        chat_id=sub.user_id,
                        text=f"❌ <b>Obuna muddati tugadi!</b>\n\n"
                        f"Kanal/Guruh: <b>{chat_row.title if chat_row else sub.chat_id}</b>\n"
                        f"Obunangiz yakunlandi va a'zolik to'xtatildi. Qayta qo'shilish uchun obunani yangilang.",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.debug("Foydalanuvchiga chiqarish xabari yetmadi: %s", e)

            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Obunalarni tekshirish fon vazifasida xatolik")


# ---------------------------------------------------------------------------
# 2. Guruh va DM Ruxsatnomalarini Tekshirish
# ---------------------------------------------------------------------------


async def check_expiring_permissions(bot: Bot) -> None:
    """Guruhda yozish va DM muloqot muddatlari tugaganini tekshiradi."""
    sessionmaker = get_sessionmaker()
    now = utcnow()

    async with sessionmaker() as session:
        try:
            # Guruhda muddati o'tgan yozish huquqlarini cheklash (Mute)
            stmt = select(ActivePermission).where(
                ActivePermission.target_type == TargetType.GROUP_CHAT,
                ActivePermission.expires_at <= now,
            )
            expired_perms = (await session.execute(stmt)).scalars().all()

            for perm in expired_perms:
                try:
                    # Yozish huquqini qaytarib olish (cheklash)
                    await bot.restrict_chat_member(
                        chat_id=perm.owner_id,
                        user_id=perm.user_id,
                        permissions=ChatPermissions(can_send_messages=False),
                    )
                except Exception as e:
                    logger.debug("Guruhda qayta cheklashda xatolik: %s", e)

            # Eski yozuvlarni tozalash
            await session.execute(
                delete(ActivePermission).where(ActivePermission.expires_at <= now)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Ruxsatnomalarni tekshirish vazifasida xato")


# ---------------------------------------------------------------------------
# 3. Boshqa standart fon vazifalari
# ---------------------------------------------------------------------------


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
    """Eski kunlik hisoblagichlarni o'chiradi."""
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
        check_expiring_subscriptions, "interval", minutes=5, args=(bot,), id="check_subs",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_expiring_permissions, "interval", minutes=10, args=(bot,), id="check_perms",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        close_sessions, "interval", minutes=10, id="close_sessions",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(cleanup_usage, "cron", hour=3, minute=30, id="cleanup_usage")
    return scheduler
