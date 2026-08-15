"""Istisnolar (whitelist / blacklist / alohida narx) va kunlik limitlar."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import AccessRuleKind
from bot.db.models import AccessRule, ChatUsage, InboxUsage
from bot.utils.timeutils import local_day_key, utcnow


# --------------------------------------------------------------------------
# Istisnolar
# --------------------------------------------------------------------------


async def get_rule(
    session: AsyncSession,
    target_id: int,
    *,
    owner_id: int = 0,
    chat_id: int = 0,
) -> AccessRule | None:
    """Amaldagi qoidani qaytaradi (muddati o'tganlari hisobga olinmaydi)."""
    stmt = select(AccessRule).where(
        AccessRule.owner_id == owner_id,
        AccessRule.chat_id == chat_id,
        AccessRule.target_id == target_id,
    )
    rule = (await session.execute(stmt)).scalar_one_or_none()
    if rule is None:
        return None
    if rule.expires_at is not None and rule.expires_at <= utcnow():
        await session.delete(rule)
        await session.flush()
        return None
    return rule


async def set_rule(
    session: AsyncSession,
    target_id: int,
    kind: str,
    *,
    owner_id: int = 0,
    chat_id: int = 0,
    price_mxtr: int = 0,
    note: str | None = None,
    expires_at: datetime | None = None,
) -> AccessRule:
    rule = await get_rule(session, target_id, owner_id=owner_id, chat_id=chat_id)
    if rule is None:
        rule = AccessRule(owner_id=owner_id, chat_id=chat_id, target_id=target_id, kind=kind)
        session.add(rule)
    rule.kind = kind
    rule.price_mxtr = price_mxtr
    rule.note = note
    rule.expires_at = expires_at
    await session.flush()
    return rule


async def remove_rule(
    session: AsyncSession, target_id: int, *, owner_id: int = 0, chat_id: int = 0
) -> bool:
    rule = await get_rule(session, target_id, owner_id=owner_id, chat_id=chat_id)
    if rule is None:
        return False
    await session.delete(rule)
    await session.flush()
    return True


async def list_rules(
    session: AsyncSession,
    *,
    owner_id: int = 0,
    chat_id: int = 0,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AccessRule]:
    stmt = select(AccessRule).where(AccessRule.owner_id == owner_id, AccessRule.chat_id == chat_id)
    if kind:
        stmt = stmt.where(AccessRule.kind == kind)
    stmt = stmt.order_by(AccessRule.id.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


async def is_blocked(
    session: AsyncSession, target_id: int, *, owner_id: int = 0, chat_id: int = 0
) -> bool:
    rule = await get_rule(session, target_id, owner_id=owner_id, chat_id=chat_id)
    return rule is not None and rule.kind == AccessRuleKind.BLOCKED


# --------------------------------------------------------------------------
# Kunlik hisoblagichlar
# --------------------------------------------------------------------------


async def _get_or_create_inbox_usage(
    session: AsyncSession, owner_id: int, sender_id: int, day: str
) -> InboxUsage:
    stmt = select(InboxUsage).where(
        InboxUsage.owner_id == owner_id,
        InboxUsage.sender_id == sender_id,
        InboxUsage.day == day,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = InboxUsage(owner_id=owner_id, sender_id=sender_id, day=day)
        session.add(row)
        await session.flush()
    return row


async def inbox_counters(
    session: AsyncSession, owner_id: int, sender_id: int, tz_offset: int
) -> tuple[int, int]:
    """(bugungi umumiy xabarlar, shu yuboruvchidan bugungi xabarlar)."""
    day = local_day_key(utcnow(), tz_offset)
    total = await _get_or_create_inbox_usage(session, owner_id, 0, day)
    per_sender = await _get_or_create_inbox_usage(session, owner_id, sender_id, day)
    return total.count, per_sender.count


async def bump_inbox_usage(
    session: AsyncSession,
    owner_id: int,
    sender_id: int,
    tz_offset: int,
    *,
    earned_mxtr: int = 0,
) -> None:
    day = local_day_key(utcnow(), tz_offset)
    total = await _get_or_create_inbox_usage(session, owner_id, 0, day)
    total.count += 1
    total.earned_mxtr += earned_mxtr
    per_sender = await _get_or_create_inbox_usage(session, owner_id, sender_id, day)
    per_sender.count += 1
    per_sender.earned_mxtr += earned_mxtr
    await session.flush()


async def get_chat_usage(
    session: AsyncSession, chat_id: int, user_id: int, tz_offset: int
) -> ChatUsage:
    day = local_day_key(utcnow(), tz_offset)
    stmt = select(ChatUsage).where(
        ChatUsage.chat_id == chat_id, ChatUsage.user_id == user_id, ChatUsage.day == day
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = ChatUsage(chat_id=chat_id, user_id=user_id, day=day)
        session.add(row)
        await session.flush()
    return row


async def chat_total_messages(session: AsyncSession, chat_id: int, user_id: int) -> int:
    """Foydalanuvchining shu guruhdagi umumiy xabarlari (birinchi N bepul uchun)."""
    from sqlalchemy import func

    stmt = select(func.coalesce(func.sum(ChatUsage.total_messages), 0)).where(
        ChatUsage.chat_id == chat_id, ChatUsage.user_id == user_id
    )
    return int((await session.execute(stmt)).scalar_one())
