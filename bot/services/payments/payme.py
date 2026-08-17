"""Payme Merchant JSON-RPC 2.0 API xizmati."""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, Optional

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import PaymentStatus
from bot.db.models import PaymentOrder
from bot.services.fulfillment import fulfill_order

logger = logging.getLogger(__name__)

# Payme xato kodlari
ERROR_INTERNAL = -32400
ERROR_INVALID_AUTH = -32504
ERROR_METHOD_NOT_FOUND = -32601
ERROR_ORDER_NOT_FOUND = -31050
ERROR_INVALID_AMOUNT = -31001
ERROR_ALREADY_PAID = -31051
ERROR_TRANSACTION_NOT_FOUND = -31003
ERROR_CANT_CANCEL = -31007


def verify_payme_auth(auth_header: Optional[str]) -> bool:
    """Payme Basic Auth tekshiruvi (Paycom:SECRET_KEY)."""
    if not settings.payme_secret_key:
        return True
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    encoded = auth_header.split(" ", 1)[1]
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
        return username == "Paycom" and password == settings.payme_secret_key
    except Exception:
        return False


class PaymeHandler:
    def __init__(self, bot: Bot, session: AsyncSession) -> None:
        self.bot = bot
        self.session = session

    async def handle_rpc(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        method = payload.get("method")
        params = payload.get("params", {})
        rpc_id = payload.get("id")

        handler = getattr(self, f"method_{method}", None)
        if not handler:
            return self._error(rpc_id, ERROR_METHOD_NOT_FOUND, "Method not found")

        try:
            result = await handler(params)
            return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
        except Exception as e:
            logger.exception("Payme RPC xatosi (%s): %s", method, e)
            return self._error(rpc_id, ERROR_INTERNAL, str(e))

    def _error(self, rpc_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": code, "message": {"uz": message, "ru": message, "en": message}},
        }

    async def method_CheckPerformTransaction(self, params: Dict[str, Any]) -> Dict[str, Any]:
        account = params.get("account", {})
        order_id = account.get("order_id")
        amount = params.get("amount", 0)  # tiyinda

        order = (
            await self.session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))
        ).scalar_one_or_none()

        if not order:
            raise Exception("Order not found")

        if order.status == PaymentStatus.PAID:
            raise Exception("Already paid")

        expected_tiyin = int(order.amount * 100)
        if amount != expected_tiyin:
            raise Exception("Incorrect amount")

        return {"allow": True}

    async def method_CreateTransaction(self, params: Dict[str, Any]) -> Dict[str, Any]:
        trans_id = params.get("id")
        time_ms = params.get("time")
        account = params.get("account", {})
        order_id = account.get("order_id")
        amount = params.get("amount", 0)

        order = (
            await self.session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))
        ).scalar_one_or_none()

        if not order:
            raise Exception("Order not found")

        if order.provider_transaction_id and order.provider_transaction_id != trans_id:
            raise Exception("Transaction ID mismatch")

        order.provider_transaction_id = trans_id
        await self.session.commit()

        create_time = int(time.time() * 1000)
        return {
            "create_time": create_time,
            "transaction": str(order.id),
            "state": 1,
        }

    async def method_PerformTransaction(self, params: Dict[str, Any]) -> Dict[str, Any]:
        trans_id = params.get("id")
        order = (
            await self.session.execute(
                select(PaymentOrder).where(PaymentOrder.provider_transaction_id == trans_id)
            )
        ).scalar_one_or_none()

        if not order:
            raise Exception("Transaction not found")

        if order.status != PaymentStatus.PAID:
            await fulfill_order(self.bot, self.session, order)

        perform_time = int(time.time() * 1000)
        return {
            "transaction": str(order.id),
            "perform_time": perform_time,
            "state": 2,
        }

    async def method_CheckTransaction(self, params: Dict[str, Any]) -> Dict[str, Any]:
        trans_id = params.get("id")
        order = (
            await self.session.execute(
                select(PaymentOrder).where(PaymentOrder.provider_transaction_id == trans_id)
            )
        ).scalar_one_or_none()

        if not order:
            raise Exception("Transaction not found")

        state = 2 if order.status == PaymentStatus.PAID else (1 if order.status == PaymentStatus.PENDING else -1)
        now_ms = int(time.time() * 1000)
        return {
            "create_time": now_ms,
            "perform_time": now_ms if state == 2 else 0,
            "cancel_time": now_ms if state == -1 else 0,
            "transaction": str(order.id),
            "state": state,
            "reason": None,
        }

    async def method_CancelTransaction(self, params: Dict[str, Any]) -> Dict[str, Any]:
        trans_id = params.get("id")
        reason = params.get("reason")
        order = (
            await self.session.execute(
                select(PaymentOrder).where(PaymentOrder.provider_transaction_id == trans_id)
            )
        ).scalar_one_or_none()

        if not order:
            raise Exception("Transaction not found")

        order.status = PaymentStatus.FAILED
        await self.session.commit()

        cancel_time = int(time.time() * 1000)
        return {
            "transaction": str(order.id),
            "cancel_time": cancel_time,
            "state": -1,
        }
