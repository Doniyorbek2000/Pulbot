"""Guruhlarda yozishni pullik qilish (Pay-to-Chat / Pay-per-Post) nazoratchisi."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import ChatMemberUpdatedFilter, Command, CommandObject, JOIN_TRANSITION
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
from bot.db.enums import ChatMode, TargetType
from bot.db.models import ActivePermission, ChatSettings, User
from bot.services import chats
from bot.services.payments.orders import (
    create_cryptobot_invoice,
    create_payment_order,
    get_click_url,
    get_payme_url,
)
from bot.utils.timeutils import utcnow

logger = logging.getLogger(__name__)

router = Router(name="group_guard")

LOCKED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)

UNLOCKED_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)


@router.message(Command("yopish", "lock"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_lock_group(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Guruhda yozishni to'liq qulflash (faqat to'lov qilganlar yoza oladi)."""
    user_id = message.from_user.id if message.from_user else 0
    member = await bot.get_chat_member(message.chat.id, user_id)
    if member.status not in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        await message.reply("❌ Bu buyruq faqat guruh adminlari uchun!")
        return

    chat_row = await chats.get_or_create(session, message.chat, owner_id=user_id)
    chat_row.enabled = True
    chat_row.mode = ChatMode.PAID
    await session.commit()

    try:
        await bot.set_chat_permissions(message.chat.id, permissions=LOCKED_PERMISSIONS)
    except Exception as e:
        logger.warning("Guruhni qulflashda xato: %s", e)

    price_sum = int(chat_row.price_mxtr / 1000 * 170) if chat_row.price_mxtr else 10000
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Yozish huquqini sotib olish",
                    url=f"https://t.me/{settings.bot_username}?start=chat_{abs(message.chat.id)}"
                    if settings.bot_username
                    else "https://t.me",
                )
            ]
        ]
    )

    await message.answer(
        f"🔒 <b>Guruhda yozish yopildi (Pullik rejim yoqildi)!</b>\n\n"
        f"Guruh a'zolari faqat to'lov qilgandan so'ng yoza olishadi.\n"
        f"💰 Tarif: <b>{price_sum:,.0f} so'm</b> / 30 kun\n\n"
        f"Guruhda yozish ruxsatini olish uchun quyidagi tugmani bosing:",
        reply_markup=markup,
        parse_mode="HTML",
    )


@router.message(Command("ochish", "unlock"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_unlock_group(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Guruhda yozishni hamma uchun ochish (bepul qilish)."""
    user_id = message.from_user.id if message.from_user else 0
    member = await bot.get_chat_member(message.chat.id, user_id)
    if member.status not in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        await message.reply("❌ Bu buyruq faqat guruh adminlari uchun!")
        return

    chat_row = await chats.get_or_create(session, message.chat, owner_id=user_id)
    chat_row.enabled = False
    chat_row.mode = ChatMode.FREE
    await session.commit()

    try:
        await bot.set_chat_permissions(message.chat.id, permissions=UNLOCKED_PERMISSIONS)
    except Exception as e:
        logger.warning("Guruhni ochishda xato: %s", e)

    await message.answer("🔓 <b>Guruhda yozish barcha a'zolar uchun ochildi (bepul rejim).</b>", parse_mode="HTML")


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_user_joined_group(event: ChatMemberUpdated, bot: Bot, session: AsyncSession) -> None:
    """Yangi a'zo guruhga kirganda guruh pullik bo'lsa uni MUTE qilish."""
    chat_id = event.chat.id
    user_id = event.from_user.id

    chat_row = (
        await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
    ).scalar_one_or_none()

    if not chat_row or not chat_row.enabled or chat_row.mode != ChatMode.PAID:
        return

    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=LOCKED_PERMISSIONS,
        )
    except Exception as e:
        logger.warning("Guruh a'zosini cheklashda xatolik: %s", e)


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def check_group_message(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Guruhda yuborilgan xabarlarni tekshirish va to'lovsiz bo'lsa o'chirish."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    if user_id == 0 or message.from_user.is_bot:
        return

    # Buyruqlarni o'tkazib yuborish
    if message.text and message.text.startswith("/"):
        return

    # Guruh sozlamalarini olish
    chat_row = (
        await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
    ).scalar_one_or_none()

    if not chat_row or not chat_row.enabled or chat_row.mode != ChatMode.PAID:
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

    # To'lov qilinmagan xabarni o'chirish va a'zoni cheklash
    try:
        await message.delete()
        await bot.restrict_chat_member(chat_id, user_id, permissions=LOCKED_PERMISSIONS)
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

    try:
        await message.answer(
            f"⚠️ {message.from_user.mention_html()}, bu guruhda yozish <b>pullik</b>.\n\n"
            f"💰 Tarif: <b>{price_sum:,.0f} so'm</b> / 30 kun\n"
            f"Yozish huquqini ochish uchun to'lovni bajaring:",
            reply_markup=markup,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug("Ogohlantirish yuborib bo'lmadi: %s", e)
