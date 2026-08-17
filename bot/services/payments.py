"""To'lovlar — hozircha Telegram Stars (XTR).

Boshqa provayderlar (Click, Payme, Uzum, karta) keyinchalik shu yerga
qo'shiladi: `Payment` yozuvi va `credit_payment()` mantiqi o'zgarmaydi,
faqat provayderga xos invoice yaratish va callback qismi qo'shiladi.
"""

from __future__ import annotations

import logging
import secrets

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import LabeledPrice, Message, SuccessfulPayment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import PaymentProvider, PaymentStatus, TxKind
from bot.db.models import Payment, User
from bot.i18n import Translator
from bot.services import app_settings, wallet
from bot.utils.money import format_amount, stars_to_mxtr
from bot.utils.timeutils import utcnow

logger = logging.getLogger(__name__)

PAYLOAD_PREFIX = "topup"


def make_payload(user_id: int) -> str:
    return f"{PAYLOAD_PREFIX}:{user_id}:{secrets.token_urlsafe(8)}"


def parse_payload(payload: str) -> int | None:
    parts = payload.split(":")
    if len(parts) >= 2 and parts[0] == PAYLOAD_PREFIX and parts[1].isdigit():
        return int(parts[1])
    return None


async def create_star_invoice(
    bot: Bot,
    session: AsyncSession,
    *,
    user: User,
    stars: int,
    chat_id: int | None = None,
) -> Message:
    """Telegram Stars invoice yuboradi va `Payment` yozuvini yaratadi."""
    stars = max(1, int(stars))
    translator = Translator(user.language)
    rate_uzs, rate_usd = await app_settings.rates(session)
    amount_mxtr = stars_to_mxtr(stars)

    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.STARS,
        status=PaymentStatus.PENDING,
        amount_mxtr=amount_mxtr,
        stars=stars,
        display_currency=user.display_currency,
        display_amount=format_amount(
            amount_mxtr, user.display_currency, rate_uzs=rate_uzs, rate_usd=rate_usd
        ),
        payload=make_payload(user.id),
    )
    session.add(payment)
    await session.flush()

    return await bot.send_invoice(
        chat_id=chat_id or user.id,
        title=translator("topup.invoice_title"),
        description=translator("topup.invoice_desc", stars=stars),
        payload=payment.payload,
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars} ⭐", amount=stars)],
        # Stars uchun provider_token bo'sh bo'lishi shart
        provider_token="",
    )


async def find_by_payload(session: AsyncSession, payload: str) -> Payment | None:
    stmt = select(Payment).where(Payment.payload == payload)
    return (await session.execute(stmt)).scalar_one_or_none()


async def credit_payment(
    session: AsyncSession,
    payment: Payment,
    successful: SuccessfulPayment,
) -> int:
    """To'lovni tasdiqlab, balansga qo'shadi. Qaytaradi: yangi balans (mXTR)."""
    if payment.status == PaymentStatus.PAID:
        logger.info("To'lov allaqachon hisoblangan: %s", payment.payload)
        current, _ = await wallet.balance(session, payment.user_id)
        return current

    payment.status = PaymentStatus.PAID
    payment.paid_at = utcnow()
    payment.telegram_charge_id = successful.telegram_payment_charge_id
    payment.provider_charge_id = successful.provider_payment_charge_id
    payment.raw = {
        "currency": successful.currency,
        "total_amount": successful.total_amount,
    }

    await wallet.credit(
        session,
        payment.user_id,
        payment.amount_mxtr,
        TxKind.TOPUP,
        ref_type="payment",
        ref_id=payment.id,
        idempotency_key=f"topup:{payment.payload}",
        note=f"{payment.stars} ⭐",
        extra={"charge_id": successful.telegram_payment_charge_id},
    )
    await session.flush()

    balance, _available = await wallet.balance(session, payment.user_id)
    return balance


async def refund_star_payment(
    bot: Bot, session: AsyncSession, payment: Payment
) -> bool:
    """To'lovni Telegram orqali qaytaradi va balansdan yechadi."""
    if payment.status != PaymentStatus.PAID or not payment.telegram_charge_id:
        return False
    try:
        await bot.refund_star_payment(
            user_id=payment.user_id,
            telegram_payment_charge_id=payment.telegram_charge_id,
        )
    except TelegramAPIError as exc:
        logger.error("Stars refund xatosi (%s): %s", payment.id, exc)
        return False

    payment.status = PaymentStatus.REFUNDED
    payment.refunded_at = utcnow()
    try:
        await wallet.debit(
            session,
            payment.user_id,
            payment.amount_mxtr,
            TxKind.TOPUP_REFUND,
            allow_locked=True,
            ref_type="payment",
            ref_id=payment.id,
            idempotency_key=f"topup_refund:{payment.payload}",
        )
    except wallet.InsufficientFunds:
        logger.warning("Refund: %s hisobida mablag' yetarli emas", payment.user_id)
    await session.flush()
    return True


async def recent_topup_mxtr(session: AsyncSession, user_id: int, hours: int) -> int:
    """Oxirgi N soatdagi to'ldirishlar (pul yechishdagi xavfsizlik uchun)."""
    from datetime import timedelta

    from sqlalchemy import func

    since = utcnow() - timedelta(hours=hours)
    stmt = select(func.coalesce(func.sum(Payment.amount_mxtr), 0)).where(
        Payment.user_id == user_id,
        Payment.status == PaymentStatus.PAID,
        Payment.paid_at >= since,
    )
    return int((await session.execute(stmt)).scalar_one())
