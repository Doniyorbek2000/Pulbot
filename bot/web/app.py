"""FastAPI asosiy web ilovasi (Webhooks + Mini App REST API)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bot.config import settings
from bot.db.session import close_db, init_db
from bot.web.routes_api import router as api_router
from bot.web.routes_click import router as click_router
from bot.web.routes_cryptobot import router as cryptobot_router
from bot.web.routes_payme import router as payme_router
from bot.web.routes_uzum import router as uzum_router

STATIC_DIR = Path(__file__).parent / "static"


def create_app(bot: Bot, dp: Dispatcher) -> FastAPI:
    """FastAPI ilovasini yaratadi va sozlaydi."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.bot = bot
        app.state.dp = dp
        await init_db()

        # Webhook o'rnatish (agar sozlangan bo'lsa)
        if settings.use_webhook:
            webhook_full_url = f"{settings.webhook_url.rstrip('/')}{settings.webhook_path}"
            await bot.set_webhook(
                url=webhook_full_url,
                secret_token=settings.webhook_secret or None,
                drop_pending_updates=True,
                allowed_updates=dp.resolve_used_update_types(),
            )

        yield

        if settings.use_webhook:
            await bot.delete_webhook()
        await close_db()

    app = FastAPI(title="PulBot API & Gateways", lifespan=lifespan)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(click_router)
    app.include_router(payme_router)
    app.include_router(uzum_router)
    app.include_router(cryptobot_router)
    app.include_router(api_router)

    # Telegram Bot Webhook endpoint
    if settings.webhook_path:
        @app.post(settings.webhook_path)
        async def telegram_webhook(request: Request):
            data = await request.json()
            update = Update.model_validate(data, context={"bot": bot})
            await dp.feed_update(bot, update)
            return {"ok": True}

    # Mini App Static Files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/app")
        @app.get("/")
        async def serve_mini_app():
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app
