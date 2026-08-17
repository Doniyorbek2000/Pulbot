"""@CryptoBot Webhook marshruti."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.session import get_session
from bot.services.payments.cryptobot import (
    process_cryptobot_webhook,
    verify_cryptobot_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments/cryptobot", tags=["CryptoBot Payment"])


@router.post("")
async def cryptobot_webhook(
    request: Request,
    crypto_pay_api_signature: str | None = Header(default=None, alias="crypto-pay-api-signature"),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """CryptoPay API webhook qabul qiluvchi."""
    raw_body = await request.body()
    if crypto_pay_api_signature:
        if not verify_cryptobot_signature(raw_body, crypto_pay_api_signature):
            logger.warning("Noto'g'ri CryptoBot imzosi!")
            raise HTTPException(status_code=400, detail="Invalid signature")

    data = await request.json()
    bot = request.app.state.bot
    success = await process_cryptobot_webhook(bot, session, data)
    return {"ok": success}
