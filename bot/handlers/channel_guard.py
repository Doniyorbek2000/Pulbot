"""Kanal va VIP Guruhlarga pullik obunani boshqarish handlerlari."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import TargetType
from bot.db.models import ChatSettings, Subscription, User
from bot.services.payments.orders import (
    create_cryptobot_invoice,
    create_payment_order,
    get_click_url,
    get_payme_url,
)

logger = logging.getLogger(__name__)

router = Router(name="channel_guard")


@router.message(Command("obuna", "sub", "vip"))
async def cmd_subscription_menu(message: Message, session: AsyncSession) -> None:
    """Mavjud pullik kanallar va VIP guruhlar ro'yxatini ko'rsatish."""
    stmt = select(ChatSettings).where(ChatSettings.enabled.is_(True))
    chats = (await session.execute(stmt)).scalars().all()

    if not chats:
        await message.answer("ℹ️ Hozircha faol pullik kanal yoki guruhlar mavjud emas.")
        return

    buttons = []
    for chat in chats:
        price_sum = int(chat.price_mxtr / 1000 * 170) if chat.price_mxtr else 30000
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"📢 {chat.title} — {price_sum:,.0f} UZS/oy",
                    callback_data=f"buy_sub:{chat.chat_id}",
                )
            ]
        )

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        "💎 <b>Pullik Kanallar va VIP Guruhlar:</b>\n\n"
        "Obuna bo'lish uchun kerakli kanal yoki guruhni tanlang:",
        reply_markup=markup,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("buy_sub:"))
async def on_buy_subscription(query: CallbackQuery, session: AsyncSession) -> None:
    """Obuna sotib olish to'lov havolalarini chiqarish."""
    chat_id = int(query.data.split(":")[1])
    user_id = query.from_user.id

    chat_row = (
        await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
    ).scalar_one_or_none()

    if not chat_row:
        await query.answer("Kanal topilmadi", show_alert=True)
        return

    price_sum = int(chat_row.price_mxtr / 1000 * 170) if chat_row.price_mxtr else 30000
    if price_sum < 1000:
        price_sum = 30000

    order = await create_payment_order(
        session,
        user_id=user_id,
        recipient_id=chat_row.owner_id,
        target_type=TargetType.CHANNEL_SUB,
        target_id=chat_id,
        amount=price_sum,
        currency="UZS",
    )
    await session.commit()

    click_url = get_click_url(order.id, price_sum)
    payme_url = get_payme_url(order.id, price_sum * 100)
    crypto_url = await create_cryptobot_invoice(order.id, round(price_sum / 12800, 2))

    buttons = [
        [
            InlineKeyboardButton(text="🔹 Click orqali to'lash", url=click_url),
            InlineKeyboardButton(text="🟢 Payme orqali to'lash", url=payme_url),
        ]
    ]
    if crypto_url:
        buttons.append([InlineKeyboardButton(text="💎 USDT / TON (@CryptoBot)", url=crypto_url)])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await query.message.answer(
        f"💳 <b>Obuna to'lovi: {chat_row.title}</b>\n\n"
        f"💰 Narxi: <b>{price_sum:,.0f} so'm</b> / 30 kun\n\n"
        f"To'lovni amalga oshirishingiz bilan sizga avtomatik 1 martalik maxsus havola beriladi.",
        reply_markup=markup,
        parse_mode="HTML",
    )
    await query.answer()
