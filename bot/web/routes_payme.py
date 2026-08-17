"""Payme Merchant JSON-RPC webhook marshruti."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.session import get_session
from bot.services.payments.payme import PaymeHandler, verify_payme_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments/payme", tags=["Payme Payment"])


@router.post("")
async def payme_rpc(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Payme JSON-RPC 2.0 endpoint."""
    if not verify_payme_auth(authorization):
        raise HTTPException(
            status_code=200,
            detail={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32504, "message": "Invalid Authorization header"},
            },
        )

    payload = await request.json()
    bot = request.app.state.bot
    handler = PaymeHandler(bot, session)
    return await handler.handle_rpc(payload)
