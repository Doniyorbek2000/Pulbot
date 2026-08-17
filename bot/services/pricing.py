"""Narxlash dvigateli.

Bitta joyda "kim, qachon, qancha to'laydi" savoliga javob beradi — ham
shaxsiy xabarlar (DM), ham guruh/kanallar uchun. Handler'lar hech qachon
narxni o'zi hisoblamaydi, faqat shu yerdagi `quote_*` funksiyalarini chaqiradi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import (
    AccessRuleKind,
    ChatMode,
    InboxMode,
    PricingUnit,
    ScheduleAction,
)
from bot.db.models import (
    ChatSchedule,
    ChatSettings,
    InboxSchedule,
    InboxSettings,
    RelaySession,
    User,
)
from bot.services import access
from bot.utils.timeutils import matches, utcnow


# --------------------------------------------------------------------------
# Natija
# --------------------------------------------------------------------------

#: Sabab kalitlari — locale fayllarida `reason.<kalit>` sifatida tarjima qilinadi
class Reason:
    SELF = "self"
    OPEN = "open"
    RULE_FREE = "rule_free"
    RULE_BLOCKED = "rule_blocked"
    RULE_PRICE = "rule_price"
    PREMIUM = "premium"
    NOT_PREMIUM = "not_premium"
    CLOSED = "closed"
    SCHEDULE_FREE = "schedule_free"
    SCHEDULE_PRICE = "schedule_price"
    SCHEDULE_CLOSED = "schedule_closed"
    SESSION_ACTIVE = "session_active"
    FIRST_FREE = "first_free"
    DAILY_QUOTA = "daily_quota"
    LIMIT_REACHED = "limit_reached"
    SENDER_LIMIT = "sender_limit"
    PRICE = "price"
    ADMIN = "admin"
    OWNER = "owner"
    BANNED = "banned"


@dataclass(slots=True)
class Quote:
    """Narx haqida yakuniy qaror."""

    allowed: bool
    price_mxtr: int = 0
    reason: str = Reason.OPEN
    currency: str = "XTR"
    unit: str = PricingUnit.PER_MESSAGE
    session_minutes: int = 0
    session_id: int | None = None
    hold_hours: int = 0
    details: dict = field(default_factory=dict)

    @property
    def is_free(self) -> bool:
        return self.allowed and self.price_mxtr <= 0

    @property
    def needs_payment(self) -> bool:
        return self.allowed and self.price_mxtr > 0


# --------------------------------------------------------------------------
# Jadval (schedule) ni qo'llash
# --------------------------------------------------------------------------


def _pick_schedule(rules, moment: datetime, tz_offset: int):
    """Eng yuqori ustuvorlikdagi mos qoidani tanlaydi."""
    best = None
    for rule in rules:
        if not rule.enabled:
            continue
        if not matches(rule.days_mask, rule.start_min, rule.end_min, moment, tz_offset):
            continue
        if best is None or rule.priority < best.priority:
            best = rule
    return best


async def _inbox_schedules(session: AsyncSession, user_id: int) -> list[InboxSchedule]:
    stmt = select(InboxSchedule).where(
        InboxSchedule.user_id == user_id, InboxSchedule.enabled.is_(True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _chat_schedules(session: AsyncSession, chat_id: int) -> list[ChatSchedule]:
    stmt = select(ChatSchedule).where(
        ChatSchedule.chat_id == chat_id, ChatSchedule.enabled.is_(True)
    )
    return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------
# Sessiyalar
# --------------------------------------------------------------------------


async def active_session(
    session: AsyncSession, sender_id: int, recipient_id: int
) -> RelaySession | None:
    stmt = (
        select(RelaySession)
        .where(
            RelaySession.sender_id == sender_id,
            RelaySession.recipient_id == recipient_id,
            RelaySession.active.is_(True),
            RelaySession.expires_at > utcnow(),
        )
        .order_by(RelaySession.id.desc())
    )
    return (await session.execute(stmt)).scalars().first()


# --------------------------------------------------------------------------
# DM narxi
# --------------------------------------------------------------------------


async def quote_dm(
    session: AsyncSession,
    sender: User,
    owner: User,
    inbox: InboxSettings,
    *,
    moment: datetime | None = None,
    had_previous: bool | None = None,
) -> Quote:
    """Yuboruvchi `sender` uchun `owner` ga yozish narxini hisoblaydi."""
    moment = moment or utcnow()
    tz = owner.tz_offset_minutes
    quote = Quote(
        allowed=True,
        currency=inbox.price_currency,
        unit=inbox.pricing_unit,
        session_minutes=inbox.session_minutes,
        hold_hours=inbox.hold_hours,
    )

    # 1. O'ziga yozish — har doim bepul
    if sender.id == owner.id:
        return Quote(allowed=True, price_mxtr=0, reason=Reason.SELF)

    # 2. Bloklangan foydalanuvchi
    if sender.is_banned:
        return Quote(allowed=False, reason=Reason.BANNED)

    # 3. Shaxsiy istisnolar — eng yuqori ustuvorlik
    rule = await access.get_rule(session, sender.id, owner_id=owner.id)
    if rule is not None:
        if rule.kind == AccessRuleKind.BLOCKED:
            return Quote(allowed=False, reason=Reason.RULE_BLOCKED)
        if rule.kind == AccessRuleKind.FREE:
            quote.reason = Reason.RULE_FREE
            return quote
        if rule.kind == AccessRuleKind.CUSTOM_PRICE:
            quote.price_mxtr = rule.price_mxtr
            quote.reason = Reason.RULE_PRICE
            return quote

    # 4. To'langan sessiya hali amal qiladimi
    if inbox.pricing_unit == PricingUnit.PER_SESSION:
        existing = await active_session(session, sender.id, owner.id)
        if existing is not None:
            quote.reason = Reason.SESSION_ACTIVE
            quote.session_id = existing.id
            quote.details["expires_at"] = existing.expires_at
            return quote

    # 5. Kunlik limitlar
    if inbox.daily_message_limit or inbox.per_sender_daily_limit:
        total, per_sender = await access.inbox_counters(session, owner.id, sender.id, tz)
        if inbox.daily_message_limit and total >= inbox.daily_message_limit:
            return Quote(allowed=False, reason=Reason.LIMIT_REACHED)
        if inbox.per_sender_daily_limit and per_sender >= inbox.per_sender_daily_limit:
            return Quote(allowed=False, reason=Reason.SENDER_LIMIT)

    # 6. Rejim
    if inbox.mode == InboxMode.CLOSED:
        return Quote(allowed=False, reason=Reason.CLOSED)

    base_price = inbox.price_mxtr

    # 7. Vaqt jadvali bazaviy narxni almashtiradi
    schedule = _pick_schedule(await _inbox_schedules(session, owner.id), moment, tz)
    schedule_reason: str | None = None
    if schedule is not None:
        if schedule.action == ScheduleAction.CLOSED:
            return Quote(allowed=False, reason=Reason.SCHEDULE_CLOSED)
        if schedule.action == ScheduleAction.FREE:
            base_price = 0
            schedule_reason = Reason.SCHEDULE_FREE
        elif schedule.action == ScheduleAction.PRICE:
            base_price = schedule.price_mxtr
            schedule_reason = Reason.SCHEDULE_PRICE
        quote.details["schedule_title"] = schedule.title

    # 8. Premium qoidalari
    if inbox.mode == InboxMode.PREMIUM_ONLY:
        if sender.is_premium:
            quote.reason = Reason.PREMIUM
            return quote
        return Quote(allowed=False, reason=Reason.NOT_PREMIUM)

    if inbox.mode == InboxMode.PREMIUM_OR_PAID and sender.is_premium:
        quote.reason = Reason.PREMIUM
        return quote

    if inbox.mode == InboxMode.OPEN:
        quote.reason = schedule_reason or Reason.OPEN
        quote.price_mxtr = base_price if schedule_reason == Reason.SCHEDULE_PRICE else 0
        return quote

    # 9. PAID / PREMIUM_OR_PAID (premium bo'lmaganlar)
    if inbox.free_for_premium and sender.is_premium:
        quote.reason = Reason.PREMIUM
        return quote

    # 10. Birinchi xabar bepul
    if inbox.free_first_message:
        if had_previous is None:
            had_previous = await _has_previous_message(session, sender.id, owner.id)
        if not had_previous:
            quote.reason = Reason.FIRST_FREE
            return quote

    quote.price_mxtr = max(0, base_price)
    quote.reason = schedule_reason or (Reason.PRICE if quote.price_mxtr > 0 else Reason.OPEN)
    return quote


async def _has_previous_message(session: AsyncSession, sender_id: int, recipient_id: int) -> bool:
    from bot.db.models import RelayMessage

    stmt = select(RelayMessage.id).where(
        RelayMessage.sender_id == sender_id, RelayMessage.recipient_id == recipient_id
    ).limit(1)
    return (await session.execute(stmt)).first() is not None


# --------------------------------------------------------------------------
# Guruh / kanal narxi
# --------------------------------------------------------------------------


async def quote_chat(
    session: AsyncSession,
    sender: User,
    chat: ChatSettings,
    *,
    content_kind: str = "text",
    is_chat_admin: bool = False,
    moment: datetime | None = None,
) -> Quote:
    """Guruhda xabar yozish narxini hisoblaydi."""
    moment = moment or utcnow()
    tz = chat.tz_offset_minutes
    quote = Quote(allowed=True, currency=chat.price_currency)

    if not chat.enabled or chat.mode == ChatMode.FREE:
        return Quote(allowed=True, price_mxtr=0, reason=Reason.OPEN)

    if sender.id == chat.owner_id:
        return Quote(allowed=True, price_mxtr=0, reason=Reason.OWNER)

    if is_chat_admin and chat.free_for_admins:
        return Quote(allowed=True, price_mxtr=0, reason=Reason.ADMIN)

    # Guruh darajasidagi istisnolar
    rule = await access.get_rule(session, sender.id, chat_id=chat.chat_id)
    if rule is not None:
        if rule.kind == AccessRuleKind.BLOCKED:
            return Quote(allowed=False, reason=Reason.RULE_BLOCKED)
        if rule.kind == AccessRuleKind.FREE:
            return Quote(allowed=True, price_mxtr=0, reason=Reason.RULE_FREE)
        if rule.kind == AccessRuleKind.CUSTOM_PRICE:
            return Quote(
                allowed=True,
                price_mxtr=rule.price_mxtr,
                reason=Reason.RULE_PRICE,
                currency=chat.price_currency,
            )

    if chat.mode == ChatMode.PREMIUM_ONLY:
        if sender.is_premium:
            return Quote(allowed=True, price_mxtr=0, reason=Reason.PREMIUM)
        return Quote(allowed=False, reason=Reason.NOT_PREMIUM)

    if chat.mode == ChatMode.PREMIUM_OR_PAID and sender.is_premium:
        return Quote(allowed=True, price_mxtr=0, reason=Reason.PREMIUM)

    if chat.free_for_premium and sender.is_premium:
        return Quote(allowed=True, price_mxtr=0, reason=Reason.PREMIUM)

    # Bazaviy narx: kontent turi bo'yicha yoki umumiy
    base_price = chat.price_mxtr
    by_content = chat.price_by_content or {}
    if content_kind in by_content:
        base_price = int(by_content[content_kind])

    # Vaqt jadvali
    schedule = _pick_schedule(await _chat_schedules(session, chat.chat_id), moment, tz)
    schedule_reason: str | None = None
    if schedule is not None:
        if schedule.action == ScheduleAction.CLOSED:
            return Quote(allowed=False, reason=Reason.SCHEDULE_CLOSED)
        if schedule.action == ScheduleAction.FREE:
            return Quote(allowed=True, price_mxtr=0, reason=Reason.SCHEDULE_FREE)
        base_price = schedule.price_mxtr
        schedule_reason = Reason.SCHEDULE_PRICE

    # Bepul kvotalar
    if chat.free_first_messages:
        total = await access.chat_total_messages(session, chat.chat_id, sender.id)
        if total < chat.free_first_messages:
            return Quote(allowed=True, price_mxtr=0, reason=Reason.FIRST_FREE)

    if chat.free_daily_quota:
        usage = await access.get_chat_usage(session, chat.chat_id, sender.id, tz)
        if usage.free_used < chat.free_daily_quota:
            return Quote(
                allowed=True,
                price_mxtr=0,
                reason=Reason.DAILY_QUOTA,
                details={
                    "used": usage.free_used,
                    "quota": chat.free_daily_quota,
                    "left": chat.free_daily_quota - usage.free_used,
                },
            )

    quote.price_mxtr = max(0, base_price)
    quote.reason = schedule_reason or (Reason.PRICE if quote.price_mxtr > 0 else Reason.OPEN)
    return quote
