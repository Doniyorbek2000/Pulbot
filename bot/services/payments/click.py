"""Click Merchant API webhooklarini qayta ishlash xizmati."""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import PaymentStatus
from bot.db.models import PaymentOrder
from bot.services.fulfillment import fulfill_order

logger = logging.getLogger(__name__)


def verify_click_sign(params: Dict[str, Any], is_complete: bool = False) -> bool:
    """Click tomonidan yuborilgan MD5 raqamli imzoni tekshiradi."""
    secret_key = settings.click_secret_key
    if not secret_key:
        logger.warning("CLICK_SECRET_KEY sozlanmagan, test rejimida sign tekshiruvi o'tkazib yuborildi.")
        return True

    click_trans_id = str(params.get("click_trans_id", ""))
    service_id = str(params.get("service_id", ""))
    merchant_trans_id = str(params.get("merchant_trans_id", ""))
    amount = str(params.get("amount", ""))
    action = str(params.get("action", ""))
    sign_time = str(params.get("sign_time", ""))
    sign_string = str(params.get("sign_string", ""))
    merchant_prepare_id = str(params.get("merchant_prepare_id", ""))

    if is_complete:
        raw = f"{click_trans_id}{service_id}{secret_key}{merchant_trans_id}{merchant_prepare_id}{amount}{action}{sign_time}"
    else:
        raw = f"{click_trans_id}{service_id}{secret_key}{merchant_trans_id}{amount}{action}{sign_time}"

    expected_sign = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return expected_sign.lower() == sign_string.lower()


async def process_click_prepare(session: AsyncSession, params: Dict[str, Any]) -> Dict[str, Any]:
    """Click Prepare so'rovi (Action 0)."""
    click_trans_id = params.get("click_trans_id")
    merchant_trans_id = params.get("merchant_trans_id")  # order_id
    amount = float(params.get("amount", 0))

    if not verify_click_sign(params, is_complete=False):
        return {"error": -1, "error_note": "SIGN_CHECK_FAILED"}

    order = (
        await session.execute(select(PaymentOrder).where(PaymentOrder.id == merchant_trans_id))
    ).scalar_one_or_none()

    if not order:
        return {"error": -5, "error_note": "ORDER_NOT_FOUND"}

    if order.status == PaymentStatus.PAID:
        return {"error": -4, "error_note": "ALREADY_PAID"}

    if abs(float(order.amount) - amount) > 0.01:
        return {"error": -2, "error_note": "INCORRECT_AMOUNT"}

    return {
        "click_trans_id": click_trans_id,
        "merchant_trans_id": merchant_trans_id,
        "merchant_prepare_id": merchant_trans_id,
        "error": 0,
        "error_note": "Success",
    }


async def process_click_complete(
    bot: Bot, session: AsyncSession, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Click Complete so'rovi (Action 1)."""
    click_trans_id = params.get("click_trans_id")
    merchant_trans_id = params.get("merchant_trans_id")
    error = int(params.get("error", 0))

    if not verify_click_sign(params, is_complete=True):
        return {"error": -1, "error_note": "SIGN_CHECK_FAILED"}

    order = (
        await session.execute(select(PaymentOrder).where(PaymentOrder.id == merchant_trans_id))
    ).scalar_one_or_none()

    if not order:
        return {"error": -5, "error_note": "ORDER_NOT_FOUND"}

    if error < 0:
        order.status = PaymentStatus.FAILED
        await session.commit()
        return {"error": -9, "error_note": "TRANSACTION_CANCELLED"}

    if order.status == PaymentStatus.PAID:
        return {
            "click_trans_id": click_trans_id,
            "merchant_trans_id": merchant_trans_id,
            "merchant_confirm_id": merchant_trans_id,
            "error": 0,
            "error_note": "Success",
        }

    order.provider_transaction_id = str(click_trans_id)
    success = await fulfill_order(bot, session, order)
    if not success:
        return {"error": -7, "error_note": "FULFILLMENT_FAILED"}

    return {
        "click_trans_id": click_trans_id,
        "merchant_trans_id": merchant_trans_id,
        "merchant_confirm_id": merchant_trans_id,
        "error": 0,
        "error_note": "Success",
    }
