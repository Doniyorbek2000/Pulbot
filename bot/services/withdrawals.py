"""Pul yechish: so'rov yaratish, tekshiruvlar, admin tasdiqlash.

Mablag' so'rov yaratilganda darhol `locked_mxtr` ga o'tkaziladi — foydalanuvchi
uni boshqa joyga sarflay olmaydi, lekin so'rov rad etilsa yo'qolmaydi ham.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import TxKind, WithdrawMethod, WithdrawStatus
from bot.db.models import User, Withdrawal
from bot.services import app_settings, payments, wallet
from bot.utils.money import apply_bps, convert, mxtr_to_stars, stars_to_mxtr
from bot.utils.timeutils import utcnow

logger = logging.getLogger(__name__)


class WithdrawError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xato (kalit + parametrlar)."""

    def __init__(self, key: str, **params) -> None:
        self.key = key
        self.params = params
        super().__init__(key)


@dataclass(slots=True)
class RiskReport:
    score: int = 0
    flags: list[str] = field(default_factory=list)

    def add(self, flag: str, points: int) -> None:
        self.flags.append(flag)
        self.score += points


DESTINATION_PATTERNS = {
    WithdrawMethod.CARD_UZS: re.compile(r"^\d{16}$"),
    WithdrawMethod.PAYME: re.compile(r"^\+?\d{9,15}$"),
    WithdrawMethod.CLICK: re.compile(r"^\+?\d{9,15}$"),
    WithdrawMethod.USDT: re.compile(r"^T[A-Za-z0-9]{33}$"),
    WithdrawMethod.STARS_GIFT: re.compile(r"^@?[A-Za-z0-9_]{5,32}$"),
}

PAYOUT_CURRENCY = {
    WithdrawMethod.CARD_UZS: "UZS",
    WithdrawMethod.PAYME: "UZS",
    WithdrawMethod.CLICK: "UZS",
    WithdrawMethod.USDT: "USD",
    WithdrawMethod.STARS_GIFT: "XTR",
}


def normalize_destination(method: str, raw: str) -> str:
    value = raw.strip().replace(" ", "").replace("-", "")
    if method == WithdrawMethod.STARS_GIFT:
        return value.lstrip("@")
    return value


def validate_destination(method: str, value: str) -> bool:
    pattern = DESTINATION_PATTERNS.get(method)
    return bool(pattern and pattern.match(value))


async def available_to_withdraw(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """(yechish mumkin bo'lgan mXTR, xavfsizlik muddatidagi mXTR).

    Yaqinda to'ldirilgan mablag' `withdraw_hold_hours` davomida ushlab turiladi —
    o'g'irlangan karta bilan to'ldirib, darhol yechib olishning oldini oladi.
    """
    wallet_row = await wallet.get_wallet(session, user_id)
    hold_hours = int(await app_settings.get(session, "withdraw_hold_hours", 72))
    recent = await payments.recent_topup_mxtr(session, user_id, hold_hours) if hold_hours else 0

    # Ishlab topilgan mablag' cheklanmaydi, faqat yaqinda to'ldirilgani cheklanadi
    earned_free = max(0, wallet_row.available_mxtr - recent)
    held = min(recent, wallet_row.available_mxtr)
    return earned_free, held


async def assess_risk(session: AsyncSession, user: User, amount_mxtr: int) -> RiskReport:
    """Oddiy anti-fraud baholash — admin qaror qabul qilishiga yordam beradi."""
    report = RiskReport()
    wallet_row = await wallet.get_wallet(session, user.id)

    if wallet_row.total_earned_mxtr < amount_mxtr:
        report.add("earned_less_than_requested", 30)
    if (utcnow() - user.created_at).days < 3:
        report.add("new_account", 25)

    hold_hours = int(await app_settings.get(session, "withdraw_hold_hours", 72))
    recent = await payments.recent_topup_mxtr(session, user.id, hold_hours)
    if recent > 0:
        report.add("recent_topup", 20)

    stmt = select(func.count(Withdrawal.id)).where(
        Withdrawal.user_id == user.id,
        Withdrawal.status == WithdrawStatus.REJECTED,
    )
    rejected = int((await session.execute(stmt)).scalar_one())
    if rejected:
        report.add(f"rejected_before:{rejected}", 15 * min(rejected, 3))

    since = utcnow() - timedelta(days=1)
    stmt = select(func.count(Withdrawal.id)).where(
        Withdrawal.user_id == user.id, Withdrawal.created_at >= since
    )
    today = int((await session.execute(stmt)).scalar_one())
    if today >= 3:
        report.add(f"many_requests_today:{today}", 20)

    return report


async def withdrawn_today_mxtr(session: AsyncSession, user_id: int) -> int:
    since = utcnow() - timedelta(days=1)
    stmt = select(func.coalesce(func.sum(Withdrawal.amount_mxtr), 0)).where(
        Withdrawal.user_id == user_id,
        Withdrawal.created_at >= since,
        Withdrawal.status.in_(
            (WithdrawStatus.PENDING, WithdrawStatus.APPROVED, WithdrawStatus.PAID)
        ),
    )
    return int((await session.execute(stmt)).scalar_one())


async def create_request(
    session: AsyncSession,
    user: User,
    *,
    amount_mxtr: int,
    method: str,
    destination: str,
    destination_name: str | None = None,
) -> Withdrawal:
    """Pul yechish so'rovini yaratadi va mablag'ni ushlab turadi."""
    if not await app_settings.get(session, "withdraw_enabled", True):
        raise WithdrawError("withdraw.disabled")

    min_stars = int(await app_settings.get(session, "min_withdraw_stars", 1000))
    min_mxtr = stars_to_mxtr(min_stars)
    if amount_mxtr < min_mxtr:
        raise WithdrawError("withdraw.not_enough", min_mxtr=min_mxtr)

    free_mxtr, held_mxtr = await available_to_withdraw(session, user.id)
    if amount_mxtr > free_mxtr:
        raise WithdrawError(
            "withdraw.not_enough", min_mxtr=min_mxtr, available_mxtr=free_mxtr, held_mxtr=held_mxtr
        )

    daily_limit = stars_to_mxtr(int(await app_settings.get(session, "withdraw_daily_limit_stars", 0)))
    if daily_limit:
        used = await withdrawn_today_mxtr(session, user.id)
        if used + amount_mxtr > daily_limit:
            raise WithdrawError("withdraw.daily_limit")

    if not validate_destination(method, destination):
        raise WithdrawError("error.invalid_number")

    fee_bps = int(await app_settings.get(session, "withdraw_fee_bps", 200))
    fee = apply_bps(amount_mxtr, fee_bps)
    net = amount_mxtr - fee

    rate_uzs, rate_usd = await app_settings.rates(session)
    payout_currency = PAYOUT_CURRENCY.get(method, "UZS")
    payout_amount = (
        str(mxtr_to_stars(net))
        if payout_currency == "XTR"
        else str(convert(net, payout_currency, rate_uzs, rate_usd))
    )

    risk = await assess_risk(session, user, amount_mxtr)

    request = Withdrawal(
        user_id=user.id,
        amount_mxtr=amount_mxtr,
        fee_mxtr=fee,
        net_mxtr=net,
        method=method,
        destination=destination,
        destination_name=destination_name,
        payout_currency=payout_currency,
        payout_amount=payout_amount,
        risk_score=risk.score,
        risk_flags={"flags": risk.flags},
    )
    session.add(request)
    await session.flush()

    # Mablag'ni ushlab turamiz
    await wallet.lock(session, user.id, amount_mxtr)
    await wallet.get_wallet(session, user.id)
    from bot.db.models import Transaction

    session.add(
        Transaction(
            user_id=user.id,
            kind=TxKind.WITHDRAW_HOLD,
            amount_mxtr=0,
            balance_after_mxtr=(await wallet.get_wallet(session, user.id)).balance_mxtr,
            ref_type="withdrawal",
            ref_id=str(request.id),
            note=f"{method} • {destination}",
            idempotency_key=f"wd_hold:{request.id}",
        )
    )
    await session.flush()
    return request


async def cancel_request(session: AsyncSession, request: Withdrawal, *, by_user: bool = True) -> None:
    """So'rovni bekor qiladi va ushlangan mablag'ni bo'shatadi."""
    if request.status not in WithdrawStatus.OPEN_STATES:
        raise WithdrawError("withdraw.cannot_cancel")

    await wallet.unlock(session, request.user_id, request.amount_mxtr)
    request.status = WithdrawStatus.CANCELED if by_user else WithdrawStatus.REJECTED
    request.processed_at = utcnow()

    from bot.db.models import Transaction

    wallet_row = await wallet.get_wallet(session, request.user_id)
    session.add(
        Transaction(
            user_id=request.user_id,
            kind=TxKind.WITHDRAW_CANCEL,
            amount_mxtr=0,
            balance_after_mxtr=wallet_row.balance_mxtr,
            ref_type="withdrawal",
            ref_id=str(request.id),
            idempotency_key=f"wd_cancel:{request.id}",
        )
    )
    await session.flush()


async def approve(session: AsyncSession, request: Withdrawal, admin_id: int) -> None:
    if request.status != WithdrawStatus.PENDING:
        raise WithdrawError("withdraw.cannot_cancel")
    request.status = WithdrawStatus.APPROVED
    request.admin_id = admin_id
    await session.flush()


async def reject(
    session: AsyncSession, request: Withdrawal, admin_id: int, reason: str
) -> None:
    if request.status not in WithdrawStatus.OPEN_STATES:
        raise WithdrawError("withdraw.cannot_cancel")
    await wallet.unlock(session, request.user_id, request.amount_mxtr)
    request.status = WithdrawStatus.REJECTED
    request.admin_id = admin_id
    request.admin_note = reason
    request.processed_at = utcnow()
    await session.flush()


async def mark_paid(
    session: AsyncSession, request: Withdrawal, admin_id: int, external_ref: str | None = None
) -> None:
    """To'lov amalga oshirildi — ushlangan mablag' hisobdan yechiladi."""
    if request.status not in WithdrawStatus.OPEN_STATES:
        raise WithdrawError("withdraw.cannot_cancel")

    # Avval bo'shatamiz, keyin yechamiz — balans va locked mos qoladi
    await wallet.unlock(session, request.user_id, request.amount_mxtr)
    await wallet.debit(
        session,
        request.user_id,
        request.amount_mxtr,
        TxKind.WITHDRAW_DONE,
        ref_type="withdrawal",
        ref_id=request.id,
        idempotency_key=f"wd_paid:{request.id}",
        note=f"{request.method} • {request.destination}",
        extra={"fee_mxtr": request.fee_mxtr, "net_mxtr": request.net_mxtr},
    )
    request.status = WithdrawStatus.PAID
    request.admin_id = admin_id
    request.external_ref = external_ref
    request.processed_at = utcnow()
    await session.flush()


async def list_for_user(
    session: AsyncSession, user_id: int, *, limit: int = 10, offset: int = 0
) -> list[Withdrawal]:
    stmt = (
        select(Withdrawal)
        .where(Withdrawal.user_id == user_id)
        .order_by(Withdrawal.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_pending(session: AsyncSession, *, limit: int = 10, offset: int = 0) -> list[Withdrawal]:
    stmt = (
        select(Withdrawal)
        .where(Withdrawal.status.in_(WithdrawStatus.OPEN_STATES))
        .order_by(Withdrawal.id.asc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_pending(session: AsyncSession) -> int:
    stmt = select(func.count(Withdrawal.id)).where(
        Withdrawal.status.in_(WithdrawStatus.OPEN_STATES)
    )
    return int((await session.execute(stmt)).scalar_one())
