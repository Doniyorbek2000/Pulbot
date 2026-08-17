"""Uzum Pay Webhook marshruti."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.session import get_session
from bot.services.payments.uzum import UzumPayHandler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments/uzum", tags=["Uzum Pay"])


@router.post("")
async def uzum_webhook(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Dict[str, Any]:
    """Uzum Pay webhook qabul qiluvchi."""
    payload = await request.json()
    logger.info("Uzum Pay webhook so'rovi: %s", payload)
    bot = request.app.state.bot
    handler = UzumPayHandler(bot, session)
    return await handler.handle_request(payload)
