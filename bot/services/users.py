"""Foydalanuvchilar bilan ishlash."""

from __future__ import annotations

import secrets
import string

from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import InboxSettings, User, Wallet
from bot.i18n import normalize
from bot.utils.timeutils import utcnow

CODE_ALPHABET = string.ascii_lowercase + string.digits
CODE_LENGTH = 8


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


async def _unique_code(session: AsyncSession) -> str:
    for _ in range(10):
        code = generate_code()
        exists = (
            await session.execute(select(User.id).where(User.public_code == code))
        ).first()
        if exists is None:
            return code
    return secrets.token_hex(8)


async def get_or_create(
    session: AsyncSession,
    tg_user: TgUser,
    *,
    referrer_id: int | None = None,
) -> User:
    """Telegram foydalanuvchisini bazaga yozadi yoki mavjudini yangilaydi."""
    user = await session.get(User, tg_user.id)
    created = user is None

    if created:
        user = User(
            id=tg_user.id,
            public_code=await _unique_code(session),
            language=normalize(tg_user.language_code),
            is_admin=tg_user.id in settings.admin_ids,
        )
        if referrer_id and referrer_id != tg_user.id:
            referrer = await session.get(User, referrer_id)
            if referrer is not None:
                user.referrer_id = referrer_id
        session.add(user)

    # Telegram profilidagi o'zgarishlarni sinxronlash
    user.username = tg_user.username
    user.first_name = tg_user.first_name or ""
    user.last_name = tg_user.last_name
    user.is_premium = bool(getattr(tg_user, "is_premium", False))
    user.last_seen_at = utcnow()
    if user.id in settings.admin_ids:
        user.is_admin = True

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        user = await session.get(User, tg_user.id)
        if user is None:
            raise

    await ensure_related(session, user)
    return user


async def ensure_related(session: AsyncSession, user: User) -> tuple[Wallet, InboxSettings]:
    """Hamyon va inbox sozlamalari mavjudligini kafolatlaydi."""
    wallet = await session.get(Wallet, user.id)
    if wallet is None:
        wallet = Wallet(user_id=user.id)
        session.add(wallet)

    inbox = await session.get(InboxSettings, user.id)
    if inbox is None:
        inbox = InboxSettings(
            user_id=user.id,
            hold_hours=settings.default_hold_hours,
            price_currency=user.display_currency,
        )
        session.add(inbox)

    await session.flush()
    return wallet, inbox


async def get_inbox(session: AsyncSession, user_id: int) -> InboxSettings:
    inbox = await session.get(InboxSettings, user_id)
    if inbox is None:
        inbox = InboxSettings(user_id=user_id, hold_hours=settings.default_hold_hours)
        session.add(inbox)
        await session.flush()
    return inbox


async def by_code(session: AsyncSession, code: str) -> User | None:
    stmt = select(User).where(User.public_code == code.lower())
    return (await session.execute(stmt)).scalar_one_or_none()


async def by_username(session: AsyncSession, username: str) -> User | None:
    username = username.lstrip("@").lower()
    stmt = select(User).where(func.lower(User.username) == username)
    return (await session.execute(stmt)).scalar_one_or_none()


async def resolve(session: AsyncSession, query: str) -> User | None:
    """ID, @username yoki public_code bo'yicha foydalanuvchini topadi."""
    query = query.strip()
    if not query:
        return None
    if query.lstrip("-").isdigit():
        return await session.get(User, int(query))
    if query.startswith("@"):
        return await by_username(session, query)
    return await by_username(session, query) or await by_code(session, query)


def deep_link(code: str) -> str:
    return f"https://t.me/{settings.bot_username}?start=u_{code}"


def referral_link(user_id: int) -> str:
    return f"https://t.me/{settings.bot_username}?start=r_{user_id}"


async def count_referrals(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count(User.id)).where(User.referrer_id == user_id)
    return int((await session.execute(stmt)).scalar_one())
