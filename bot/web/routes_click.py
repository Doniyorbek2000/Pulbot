"""Click Merchant webhook marshrutlari."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.session import get_session
from bot.services.payments.click import process_click_complete, process_click_prepare

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments/click", tags=["Click Payment"])


@router.post("/prepare")
async def click_prepare(request: Request, session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Click Prepare so'rovi."""
    form_data = await request.form()
    params = dict(form_data)
    logger.info("Click prepare so'rovi: %s", params)
    return await process_click_prepare(session, params)


@router.post("/complete")
async def click_complete(request: Request, session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Click Complete so'rovi."""
    form_data = await request.form()
    params = dict(form_data)
    bot = request.app.state.bot
    logger.info("Click complete so'rovi: %s", params)
    return await process_click_complete(bot, session, params)
