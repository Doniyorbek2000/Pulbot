"""Guruh va kanallar bilan ishlash."""

from __future__ import annotations

import logging

from aiogram.types import Chat
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ChatMode, TxKind
from bot.db.models import ChatSchedule, ChatSettings, ChatUsage, User
from bot.services import access, app_settings, wallet
from bot.services.pricing import Quote
from bot.utils.timeutils import local_day_key, utcnow

logger = logging.getLogger(__name__)


async def get_or_create(session: AsyncSession, chat: Chat, *, owner_id: int | None = None) -> ChatSettings:
    row = await session.get(ChatSettings, chat.id)
    if row is None:
        row = ChatSettings(
            chat_id=chat.id,
            chat_type=chat.type,
            title=chat.title or "",
            username=chat.username,
            owner_id=owner_id,
        )
        session.add(row)
    else:
        row.title = chat.title or row.title
        row.username = chat.username
        row.chat_type = chat.type
        if owner_id and row.owner_id is None:
            row.owner_id = owner_id
    await session.flush()
    return row


async def list_for_owner(session: AsyncSession, owner_id: int) -> list[ChatSettings]:
    stmt = (
        select(ChatSettings)
        .where(ChatSettings.owner_id == owner_id)
        .order_by(ChatSettings.title)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_schedules(session: AsyncSession, chat_id: int) -> int:
    stmt = select(func.count(ChatSchedule.id)).where(ChatSchedule.chat_id == chat_id)
    return int((await session.execute(stmt)).scalar_one())


async def charge_for_message(
    session: AsyncSession,
    *,
    chat: ChatSettings,
    sender: User,
    quote: Quote,
    message_id: int,
) -> tuple[bool, int]:
    """Guruhdagi xabar uchun pul yechadi.

    Qaytaradi: (muvaffaqiyatli, yechilgan summa).
    Escrow ishlatilmaydi — guruhda pul darhol egasiga o'tadi.
    """
    usage = await access.get_chat_usage(session, chat.chat_id, sender.id, chat.tz_offset_minutes)
    usage.total_messages += 1

    if quote.price_mxtr <= 0:
        # Bepul kvotadan foydalanildi
        if quote.reason == "daily_quota":
            usage.free_used += 1
        await session.flush()
        return True, 0

    if chat.owner_id is None:
        # Egasi bog'lanmagan — pul yechmaymiz, aks holda mablag' yo'qoladi
        logger.warning("Guruh %s egasiz — to'lov o'tkazilmadi", chat.chat_id)
        await session.flush()
        return True, 0

    commission = await app_settings.commission_bps(session)
    # Guruh egasi ulushi kamaytirilgan bo'lsa, farq ham platformaga qoladi
    effective_bps = min(10_000, commission + max(0, 10_000 - chat.owner_share_bps))

    try:
        result = await wallet.transfer(
            session,
            sender.id,
            chat.owner_id,
            quote.price_mxtr,
            effective_bps,
            spend_kind=TxKind.CHAT_SPEND,
            earn_kind=TxKind.CHAT_EARN,
            ref_type="chat_msg",
            ref_id=f"{chat.chat_id}:{message_id}",
            chat_id=chat.chat_id,
        )
    except wallet.InsufficientFunds:
        await session.flush()
        return False, 0

    usage.paid_count += 1
    usage.spent_mxtr += quote.price_mxtr
    chat.total_earned_mxtr += result.net_mxtr
    chat.total_messages_paid += 1
    await session.flush()
    return True, quote.price_mxtr


async def stats(session: AsyncSession, chat_id: int) -> dict:
    day = local_day_key(utcnow(), 300)
    stmt = select(
        func.coalesce(func.sum(ChatUsage.paid_count), 0),
        func.coalesce(func.sum(ChatUsage.spent_mxtr), 0),
    ).where(ChatUsage.chat_id == chat_id, ChatUsage.day == day)
    paid_today, spent_today = (await session.execute(stmt)).one()
    return {"paid_today": int(paid_today), "spent_today": int(spent_today)}


def is_paid_mode(chat: ChatSettings) -> bool:
    return chat.enabled and chat.mode != ChatMode.FREE
