"""@CryptoBot Webhook imzosini tekshirish va to'lovni tasdiqlash xizmati."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import PaymentOrder
from bot.services.fulfillment import fulfill_order

logger = logging.getLogger(__name__)


def verify_cryptobot_signature(raw_body: bytes, signature_header: str) -> bool:
    """CryptoPay webhook HMAC-SHA256 imzosini tekshiradi."""
    token = settings.cryptobot_token
    if not token:
        logger.warning("CRYPTOBOT_TOKEN mavjud emas")
        return True

    secret = hashlib.sha256(token.encode("utf-8")).digest()
    calc_sig = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return calc_sig.lower() == signature_header.lower()


async def process_cryptobot_webhook(
    bot: Bot, session: AsyncSession, data: Dict[str, Any]
) -> bool:
    """CryptoBot invoice_paid hodisasini qayta ishlaydi."""
    update_type = data.get("update_type")
    payload = data.get("payload", {})

    if update_type != "invoice_paid":
        logger.info("CryptoBot boshqa hodisa: %s", update_type)
        return True

    order_id = payload.get("payload")
    invoice_id = payload.get("invoice_id")
    status = payload.get("status")

    if status != "paid" or not order_id:
        return True

    order = (
        await session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))
    ).scalar_one_or_none()

    if not order:
        logger.warning("CryptoBot order topilmadi: %s", order_id)
        return False

    order.provider_transaction_id = str(invoice_id)
    return await fulfill_order(bot, session, order)
