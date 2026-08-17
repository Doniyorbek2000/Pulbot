"""Uzum Pay (Uzum Bank) Merchant API integratsiyasi va webhook qayta ishlovchisi."""

from __future__ import annotations

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


class UzumPayHandler:
    def __init__(self, bot: Bot, session: AsyncSession) -> None:
        self.bot = bot
        self.session = session

    async def handle_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Uzum Pay operatsiyalarini boshqarish."""
        action = payload.get("action")
        trans_id = str(payload.get("transId", ""))
        order_id = str(payload.get("merchantTransId", payload.get("orderId", "")))
        amount = int(payload.get("amount", 0))  # tiyinda

        order = (
            await self.session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))
        ).scalar_one_or_none()

        if not order and action != "status":
            return {
                "status": "FAILED",
                "errorCode": "ORDER_NOT_FOUND",
                "errorMessage": "Buyurtma topilmadi",
            }

        if action == "check":
            # Narx va holatni tekshirish
            if order.status == PaymentStatus.PAID:
                return {
                    "status": "FAILED",
                    "errorCode": "ALREADY_PAID",
                    "errorMessage": "Buyurtma allaqachon to'langan",
                }
            expected_tiyin = int(order.amount * 100)
            if amount != expected_tiyin:
                return {
                    "status": "FAILED",
                    "errorCode": "INVALID_AMOUNT",
                    "errorMessage": "Noto'g'ri summa",
                }
            return {"status": "OK", "data": {"orderId": order.id}}

        elif action in ("create", "pay", "confirm"):
            if order.status != PaymentStatus.PAID:
                order.provider_transaction_id = trans_id
                await fulfill_order(self.bot, self.session, order)

            return {
                "status": "CONFIRMED",
                "transId": trans_id,
                "merchantTransId": order.id,
            }

        elif action in ("cancel", "reverse"):
            order.status = PaymentStatus.FAILED
            await self.session.commit()
            return {"status": "REVERSED", "transId": trans_id}

        elif action == "status":
            if not order:
                return {"status": "NOT_FOUND"}
            return {
                "status": "CONFIRMED" if order.status == PaymentStatus.PAID else "PENDING",
                "merchantTransId": order.id,
            }

        return {"status": "FAILED", "errorCode": "UNKNOWN_ACTION"}
