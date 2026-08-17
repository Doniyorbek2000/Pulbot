"""Guruhlarda yozishni pullik qilish (Pay-to-Chat / Pay-per-Post) nazoratchisi."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.types import (
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import TargetType
from bot.db.models import ActivePermission, ChatSettings
from bot.services.payments.orders import (
    create_cryptobot_invoice,
    create_payment_order,
    get_click_url,
    get_payme_url,
)
from bot.utils.timeutils import utcnow

logger = logging.getLogger(__name__)

router = Router(name="group_guard")


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_user_joined_group(event: ChatMemberUpdated, bot: Bot, session: AsyncSession) -> None:
    """Yangi a'zo guruhga kirganda uni standart holatda MUTE qilish."""
    chat_id = event.chat.id
    user_id = event.from_user.id

    chat_row = (
        await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
    ).scalar_one_or_none()

    if not chat_row or not chat_row.enabled:
        return

    # Yangi a'zoni cheklash
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
        )
        logger.info("Yangi a'zo %s guruhda (%s) cheklandi", user_id, chat_id)
    except Exception as e:
        logger.warning("Guruh a'zosini cheklashda xatolik: %s", e)


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def check_group_message(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Guruhda yuborilgan xabarlarni tekshirish va to'lovsiz bo'lsa o'chirish."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    if user_id == 0 or message.from_user.is_bot:
        return

    # Guruh sozlamalarini olish
    chat_row = (
        await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
    ).scalar_one_or_none()

    if not chat_row or not chat_row.enabled:
        return

    # Adminlarni tekshirish
    if chat_row.free_for_admins:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status in ("creator", "administrator"):
                return
        except Exception:
            pass

    # Faol ruxsatnomani tekshirish
    now = utcnow()
    stmt = select(ActivePermission).where(
        ActivePermission.target_type == TargetType.GROUP_CHAT,
        ActivePermission.owner_id == chat_id,
        ActivePermission.user_id == user_id,
        ActivePermission.expires_at > now,
    )
    perm = (await session.execute(stmt)).scalar_one_or_none()
    if perm:
        # Ruxsat mavjud
        return

    # To'lov qilinmagan xabarni o'chirish
    try:
        await message.delete()
    except Exception as e:
        logger.debug("Guruh xabarini o'chirib bo'lmadi: %s", e)

    # To'lov havolasini yaratish
    price_sum = int(chat_row.price_mxtr / 1000 * 170) if chat_row.price_mxtr else 10000
    if price_sum < 1000:
        price_sum = 10000

    order = await create_payment_order(
        session,
        user_id=user_id,
        recipient_id=chat_row.owner_id,
        target_type=TargetType.GROUP_CHAT,
        target_id=chat_id,
        amount=price_sum,
        currency="UZS",
    )
    await session.commit()

    click_url = get_click_url(order.id, price_sum)
    payme_url = get_payme_url(order.id, price_sum * 100)

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔹 Click orqali to'lash", url=click_url),
                InlineKeyboardButton(text="🟢 Payme orqali to'lash", url=payme_url),
            ]
        ]
    )

    warn_msg = None
    try:
        warn_msg = await message.answer(
            f"⚠️ {message.from_user.mention_html()}, bu guruhda yozish <b>pullik</b>.\n\n"
            f"💰 Tarif: <b>{price_sum:,.0f} so'm</b> / 30 kun\n"
            f"Yozish huquqini faollashtirish uchun to'lovni bajaring:",
            reply_markup=markup,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug("Ogohlantirish yuborib bo'lmadi: %s", e)
