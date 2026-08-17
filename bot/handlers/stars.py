"""Telegram Stars (XTR) to'lovlarini qabul qilish handlerlari."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import PaymentProvider, PaymentStatus, TargetType
from bot.db.models import PaymentOrder
from bot.services.fulfillment import fulfill_order
from bot.services.payments.orders import create_payment_order

logger = logging.getLogger(__name__)

router = Router(name="stars_payment")


async def send_stars_invoice(
    bot: Bot,
    chat_id: int,
    user_id: int,
    order: PaymentOrder,
    title: str,
    description: str,
    stars_amount: int,
) -> None:
    """Telegram Stars fakturasini yuborish."""
    prices = [LabeledPrice(label="Telegram Stars", amount=stars_amount)]
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=f"stars:{order.id}",
        currency="XTR",
        prices=prices,
        provider_token="",  # Telegram Stars uchun bo'sh
    )


@router.pre_checkout_query()
async def on_pre_checkout_query(query: PreCheckoutQuery) -> None:
    """Telegram Stars pre-checkout so'rovini tasdiqlash."""
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_stars_payment(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Telegram Stars to'lovi muvaffaqiyatli o'tganda."""
    payment_info = message.successful_payment
    payload = payment_info.invoice_payload
    telegram_charge_id = payment_info.telegram_payment_charge_id

    if not payload.startswith("stars:"):
        return

    order_id = payload.split(":", 1)[1]
    order = (
        await session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))
    ).scalar_one_or_none()

    if not order:
        logger.warning("Stars to'lovida order topilmadi: %s", order_id)
        return

    order.provider_transaction_id = telegram_charge_id
    await fulfill_order(bot, session, order)
