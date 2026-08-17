"""Testlar uchun umumiy fixture'lar."""

from __future__ import annotations

import os

os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BOT_USERNAME", "TestPulBot")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.base import Base
from bot.db.models import InboxSettings, User, Wallet
from bot.i18n import load_locales
from bot.services import app_settings

load_locales()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def session():
    """Har bir test uchun toza xotiradagi baza."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    app_settings.invalidate()
    async with maker() as db:
        yield db
    await engine.dispose()
    app_settings.invalidate()


@pytest_asyncio.fixture
async def make_user(session):
    """Foydalanuvchi + hamyon + inbox yaratuvchi yordamchi."""
    counter = {"n": 0}

    async def _make(
        user_id: int | None = None,
        *,
        balance_mxtr: int = 0,
        is_premium: bool = False,
        **kwargs,
    ) -> User:
        counter["n"] += 1
        uid = user_id if user_id is not None else 1000 + counter["n"]
        user = User(
            id=uid,
            public_code=f"code{uid}",
            first_name=f"User{uid}",
            is_premium=is_premium,
            **kwargs,
        )
        session.add(user)
        session.add(Wallet(user_id=uid, balance_mxtr=balance_mxtr))
        session.add(InboxSettings(user_id=uid))
        await session.flush()
        return user

    return _make
