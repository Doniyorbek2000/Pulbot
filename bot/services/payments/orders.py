"""To'lov buyurtmalarini yaratish va turli to'lov shlyuzlari havolalarini shakllantirish."""

from __future__ import annotations

import base64
import hashlib
import logging
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import PaymentProvider, PaymentStatus, TargetType
from bot.db.models import PaymentOrder

logger = logging.getLogger(__name__)


async def create_payment_order(
    session: AsyncSession,
    *,
    user_id: int,
    target_type: str,
    target_id: int,
    amount: int,
    currency: str = "UZS",
    recipient_id: int | None = None,
    provider: str = PaymentProvider.CLICK,
    extra_payload: dict | None = None,
) -> PaymentOrder:
    """Yangi to'lov buyurtmasini yaratadi."""
    order_id = str(uuid.uuid4())
    order = PaymentOrder(
        id=order_id,
        user_id=user_id,
        recipient_id=recipient_id,
        target_type=target_type,
        target_id=target_id,
        provider=provider,
        amount=amount,
        currency=currency,
        status=PaymentStatus.PENDING,
        extra_payload=extra_payload or {},
    )
    session.add(order)
    await session.flush()
    return order


def get_click_url(order_id: str, amount: int, return_url: str = "") -> str:
    """Click to'lov havolasini generatsiya qiladi."""
    service_id = settings.click_service_id or "12345"
    merchant_id = settings.click_merchant_id or "12345"
    # amount so'mda
    url = (
        f"https://my.click.uz/services/pay"
        f"?service_id={service_id}&merchant_id={merchant_id}&amount={amount}&transaction_param={order_id}"
    )
    if return_url:
        url += f"&return_url={return_url}"
    return url


def get_payme_url(order_id: str, amount_tiyin: int) -> str:
    """Payme to'lov havolasini (Base64 params) generatsiya qiladi."""
    merchant_id = settings.payme_merchant_id or "640000000000000000000000"
    params = f"m={merchant_id};ac.order_id={order_id};a={amount_tiyin}"
    encoded = base64.b64encode(params.encode()).decode()
    return f"https://checkout.paycom.uz/{encoded}"


async def create_cryptobot_invoice(
    order_id: str, amount_usd: float, description: str = "PulBot to'lovi"
) -> str | None:
    """CryptoBot API orqali to'lov havolasini oladi."""
    if not settings.cryptobot_token:
        logger.warning("CRYPTOBOT_TOKEN sozlanmagan")
        return None

    base_url = (
        "https://testnet-pay.crypt.bot/api"
        if settings.cryptobot_net == "testnet"
        else "https://pay.crypt.bot/api"
    )
    headers = {"Crypto-Pay-API-Token": settings.cryptobot_token}
    payload = {
        "asset": "USDT",
        "amount": str(amount_usd),
        "description": description,
        "payload": order_id,
        "expires_in": 3600,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{base_url}/createInvoice", headers=headers, json=payload)
            data = resp.json()
            if data.get("ok"):
                return data["result"]["bot_invoice_url"]
            logger.error("CryptoBot API xatosi: %s", data)
    except Exception as e:
        logger.exception("CryptoBot invoice yaratishda xato: %s", e)
    return None
