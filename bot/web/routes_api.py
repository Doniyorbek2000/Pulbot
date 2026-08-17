"""Telegram Mini App uchun REST API marshrutlari."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.parse
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import AccessRuleKind, InboxMode, WithdrawMethod
from bot.db.models import (
    AccessRule,
    ActivePermission,
    ChatSettings,
    InboxSettings,
    Subscription,
    User,
    Wallet,
    Withdrawal,
)
from bot.db.session import get_session
from bot.services import wallet, withdrawals
from bot.utils.money import format_amount, stars_to_mxtr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Mini App API"])


# ---------------------------------------------------------------------------
# Xavfsizlik: Telegram WebApp initData tekshirish
# ---------------------------------------------------------------------------


def validate_telegram_init_data(init_data: str) -> Optional[Dict[str, Any]]:
    """Telegram WebApp initData HMAC-SHA256 tekshiruvi."""
    if not init_data:
        return None
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        hash_to_check = parsed_data.pop("hash", None)
        if not hash_to_check:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", settings.bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(
            secret_key, data_check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        if calculated_hash != hash_to_check:
            return None

        user_data = json.loads(parsed_data.get("user", "{}"))
        return user_data
    except Exception as e:
        logger.warning("initData tekshirishda xato: %s", e)
        return None


async def get_current_user(
    x_telegram_init_data: str = Header(..., alias="X-Telegram-Init-Data"),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Headerdagi initData orqali joriy foydalanuvchini aniqlaydi."""
    user_info = validate_telegram_init_data(x_telegram_init_data)
    if not user_info or "id" not in user_info:
        raise HTTPException(status_code=401, detail="Invalid Telegram Init Data")

    user_id = int(user_info["id"])
    stmt = select(User).where(User.id == user_id)
    user = (await session.execute(stmt)).scalar_one_or_none()

    if not user:
        user = User(
            id=user_id,
            username=user_info.get("username"),
            first_name=user_info.get("first_name", ""),
            last_name=user_info.get("last_name"),
            public_code=f"u_{user_id}",
        )
        session.add(user)
        session.add(Wallet(user_id=user_id))
        session.add(InboxSettings(user_id=user_id))
        await session.commit()

    return user


# ---------------------------------------------------------------------------
# Modellar (Pydantic)
# ---------------------------------------------------------------------------


class DMSettingsUpdate(BaseModel):
    enabled: bool
    pricing_unit: str = "session"  # per_message, session, monthly
    price_sum: int
    session_hours: int = 24
    welcome_text: Optional[str] = None


class WhitelistAdd(BaseModel):
    target_id: int
    reason: Optional[str] = None


class WithdrawRequest(BaseModel):
    amount_sum: int
    method: str
    destination: str
    destination_name: Optional[str] = None


# ---------------------------------------------------------------------------
# API Marshrutlari
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def get_dashboard(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> Dict[str, Any]:
    """Boshqaruv paneli uchun barcha asosiy ko'rsatkichlarni yuklaydi."""
    wallet_row = await wallet.get_wallet(session, user.id)

    # Inbox sozlamalari
    inbox = (
        await session.execute(select(InboxSettings).where(InboxSettings.user_id == user.id))
    ).scalar_one_or_none()

    # Monetizatsiya qilingan guruh/kanallar soni
    chats_count = (
        await session.execute(
            select(func.count(ChatSettings.chat_id)).where(ChatSettings.owner_id == user.id)
        )
    ).scalar() or 0

    # Faol obunachilar soni
    subs_count = (
        await session.execute(
            select(func.count(Subscription.id))
            .join(ChatSettings, ChatSettings.chat_id == Subscription.chat_id)
            .where(ChatSettings.owner_id == user.id, Subscription.is_active.is_(True))
        )
    ).scalar() or 0

    # DM narxi so'mda
    dm_price_sum = int(inbox.price_mxtr / 1000 * 170) if inbox and inbox.price_mxtr else 10000

    return {
        "user": {
            "id": user.id,
            "name": user.full_name,
            "username": user.username,
            "business_enabled": user.business_enabled,
        },
        "balance": {
            "available_mxtr": wallet_row.available_mxtr,
            "available_sum": int(wallet_row.available_mxtr / 1000 * 170),
            "earned_sum": int(wallet_row.total_earned_mxtr / 1000 * 170),
            "locked_sum": int(wallet_row.locked_mxtr / 1000 * 170),
        },
        "dm_settings": {
            "enabled": inbox.mode == InboxMode.PAID if inbox else False,
            "pricing_unit": inbox.pricing_unit if inbox and inbox.pricing_unit else "session",
            "price_sum": dm_price_sum,
            "session_hours": inbox.session_minutes // 60 if inbox else 24,
            "welcome_text": inbox.welcome_text if inbox else "",
        },
        "stats": {
            "monetized_chats": chats_count,
            "active_subscribers": subs_count,
        },
    }


@router.post("/settings/dm")
async def update_dm_settings(
    body: DMSettingsUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Shaxsiy chat (DM) narxi va paywall parametrlarini saqlash."""
    inbox = (
        await session.execute(select(InboxSettings).where(InboxSettings.user_id == user.id))
    ).scalar_one_or_none()

    if not inbox:
        inbox = InboxSettings(user_id=user.id)
        session.add(inbox)

    inbox.mode = InboxMode.PAID if body.enabled else InboxMode.OPEN
    inbox.pricing_unit = body.pricing_unit
    # so'mdan mXTR ga: (sum / 170) * 1000
    inbox.price_mxtr = int(body.price_sum / 170 * 1000)
    inbox.session_minutes = body.session_hours * 60
    if body.welcome_text is not None:
        inbox.welcome_text = body.welcome_text.strip() or None

    await session.commit()

    return {"ok": True, "message": "Sozlamalar muvaffaqiyatli saqlandi"}


@router.get("/chats")
async def get_user_chats(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> List[Dict[str, Any]]:
    """Foydalanuvchining pullik guruh va kanallari ro'yxati."""
    stmt = select(ChatSettings).where(ChatSettings.owner_id == user.id)
    chats = (await session.execute(stmt)).scalars().all()

    return [
        {
            "chat_id": chat.chat_id,
            "title": chat.title,
            "chat_type": chat.chat_type,
            "enabled": chat.enabled,
            "mode": chat.mode,
            "price_sum": int(chat.price_mxtr / 1000 * 170),
            "earned_sum": int(chat.total_earned_mxtr / 1000 * 170),
        }
        for chat in chats
    ]


@router.get("/whitelist")
async def get_whitelist(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> List[Dict[str, Any]]:
    """Oq ro'yxatdagi istisnolar."""
    stmt = select(AccessRule).where(
        AccessRule.owner_id == user.id, AccessRule.kind == AccessRuleKind.FREE
    )
    rules = (await session.execute(stmt)).scalars().all()

    return [
        {"id": r.id, "target_id": r.target_id, "note": r.note, "created_at": str(r.created_at)}
        for r in rules
    ]


@router.post("/whitelist")
async def add_whitelist(
    body: WhitelistAdd,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Oq ro'yxatga yangi foydalanuvchi qo'shish."""
    rule = AccessRule(
        owner_id=user.id,
        target_id=body.target_id,
        kind=AccessRuleKind.FREE,
        note=body.reason or "Mini App orqali qo'shildi",
    )
    session.add(rule)
    await session.commit()
    return {"ok": True, "id": rule.id}


@router.delete("/whitelist/{rule_id}")
async def delete_whitelist(
    rule_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Oq ro'yxatdan o'chirish."""
    stmt = select(AccessRule).where(AccessRule.id == rule_id, AccessRule.owner_id == user.id)
    rule = (await session.execute(stmt)).scalar_one_or_none()
    if rule:
        await session.delete(rule)
        await session.commit()
    return {"ok": True}


@router.post("/withdraw")
async def request_payout(
    body: WithdrawRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Pul yechish so'rovini yuborish."""
    amount_mxtr = int((body.amount_sum / 170) * 1000)

    try:
        withdrawal_row = await withdrawals.create_request(
            session,
            user=user,
            amount_mxtr=amount_mxtr,
            method=body.method,
            destination=body.destination,
            destination_name=body.destination_name,
        )
        await session.commit()
        return {"ok": True, "withdrawal_id": withdrawal_row.id}
    except Exception as e:
        logger.warning("Pul yechishda xato: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
