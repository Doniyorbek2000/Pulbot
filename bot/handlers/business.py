"""Telegram Business API orqali shaxsiy chatlarni (DM) avtomatlashtirish va pullik qilish."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.types import (
    BusinessConnection,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import AccessRuleKind, InboxMode, PaymentProvider, TargetType
from bot.db.models import (
    AccessRule,
    ActivePermission,
    InboxSettings,
    User,
)
from bot.i18n import get
from bot.services.payments.orders import (
    create_cryptobot_invoice,
    create_payment_order,
    get_click_url,
    get_payme_url,
)
from bot.utils.timeutils import utcnow

logger = logging.getLogger(__name__)

router = Router(name="business")


# ---------------------------------------------------------------------------
# 1. Telegram Business Ulanishi
# ---------------------------------------------------------------------------


@router.business_connection()
async def handle_business_connection(event: BusinessConnection, session: AsyncSession, bot: Bot) -> None:
    """Foydalanuvchi botni Telegram Business orqali ulaganda chaqiriladi."""
    logger.info("BUSINESS CONNECTION EVENT: user_id=%s, connection_id=%s, is_enabled=%s", event.user.id, event.id, event.is_enabled)
    stmt = select(User).where(User.id == event.user.id)
    user = (await session.execute(stmt)).scalar_one_or_none()

    if user is None:
        user = User(
            id=event.user.id,
            username=event.user.username,
            first_name=event.user.first_name,
            last_name=event.user.last_name,
            business_connection_id=event.id,
            business_enabled=event.is_enabled,
            public_code=f"u_{event.user.id}",
        )
        session.add(user)
    else:
        user.business_connection_id = event.id
        user.business_enabled = event.is_enabled

    # Inbox sozlamalarini ham avtomatik pullik rejimga o'tkazamiz
    inbox_stmt = select(InboxSettings).where(InboxSettings.user_id == event.user.id)
    inbox = (await session.execute(inbox_stmt)).scalar_one_or_none()
    if not inbox:
        inbox = InboxSettings(
            user_id=event.user.id,
            mode=InboxMode.PAID,
            price_mxtr=58823,
            session_minutes=1440,
        )
        session.add(inbox)
    elif not inbox.mode or inbox.mode == InboxMode.OPEN:
        inbox.mode = InboxMode.PAID

    await session.commit()

    if event.is_enabled:
        text = (
            f"💼 <b>Telegram Business muvaffaqiyatli ulandi!</b>\n\n"
            f"Endi bot shaxsiy chatlaringizga kelgan yangi xabarlarni avtomatik tarzda "
            f"nazorat qiladi va to'lov qilmaganlarga to'lov havolasini yuboradi.\n\n"
            f"💰 Standart tarif: <b>10,000 so'm / 24 soat</b>\n"
            f"Shaxsiy narx va sozlamalarni o'zgartirish uchun boshqaruv panelini oching."
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Sozlamalar va Narxlar (Mini App)",
                        url=f"https://t.me/{settings.bot_username}/app"
                        if settings.bot_username
                        else "https://t.me",
                    )
                ]
            ]
        )
        try:
            await bot.send_message(chat_id=event.user.id, text=text, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            logger.debug("Business egasiga xabar yetmadi: %s", e)


# ---------------------------------------------------------------------------
# 2. Shaxsiy Chat Xabarlarini Ushlab Qolish (DM Paywall)
# ---------------------------------------------------------------------------


async def _backup_media_to_owner(message: Message, bot: Bot, owner_id: int) -> None:
    """O'chib ketadigan (view-once) yoki oddiy rasm/video/ovozli xabarlarni egasining botiga avtomatik saqlash."""
    if not (message.photo or message.video or message.video_note or message.voice or message.document):
        return

    sender = message.from_user
    sender_name = sender.full_name if sender else "Noma'lum"
    username_str = f"@{sender.username}" if sender and sender.username else f"ID: {sender.id if sender else '0'}"
    time_str = utcnow().strftime("%H:%M:%S, %d.%m.%Y")
    
    caption = (
        f"📸 <b>Saqlangan media xabari (Avto-arxiv)</b>\n\n"
        f"👤 <b>Kimdan:</b> {sender_name} ({username_str})\n"
        f"⏱ <b>Vaqt:</b> {time_str}\n"
    )
    if message.caption:
        caption += f"📝 <b>Izoh:</b> {message.caption}\n"

    try:
        if message.photo:
            await bot.send_photo(
                chat_id=owner_id,
                photo=message.photo[-1].file_id,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.video:
            await bot.send_video(
                chat_id=owner_id,
                video=message.video.file_id,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.video_note:
            await bot.send_message(chat_id=owner_id, text=caption, parse_mode="HTML")
            await bot.send_video_note(chat_id=owner_id, video_note=message.video_note.file_id)
        elif message.voice:
            await bot.send_voice(
                chat_id=owner_id,
                voice=message.voice.file_id,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.document:
            await bot.send_document(
                chat_id=owner_id,
                document=message.document.file_id,
                caption=caption,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.debug("Media arxivlashda xatolik: %s", e)


@router.business_message()
async def handle_business_message(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Telegram Business chatiga kelgan har qanday xabarni tekshiradi."""
    if not message.business_connection_id:
        return

    logger.info(
        "BUSINESS MESSAGE: conn_id=%s, sender=%s, chat=%s, text=%s",
        message.business_connection_id,
        message.from_user.id if message.from_user else None,
        message.chat.id,
        message.text,
    )

    # Business egasini topish
    stmt = select(User).where(User.business_connection_id == message.business_connection_id)
    owner = (await session.execute(stmt)).scalar_one_or_none()
    
    if not owner:
        # Eng oxirgi faol admin/user ni topish
        stmt_admin = select(User).where(User.id != message.from_user.id if message.from_user else True).order_by(User.created_at.desc())
        owner = (await session.execute(stmt_admin)).scalars().first()
        if owner:
            owner.business_connection_id = message.business_connection_id
            owner.business_enabled = True
            await session.commit()

    if not owner:
        logger.warning("Business egasi topilmadi: conn_id=%s", message.business_connection_id)
        return

    sender_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id

    # O'chib ketadigan rasm/video/ovozlarni egasiga avtomatik arxivlash
    if sender_id != owner.id:
        await _backup_media_to_owner(message, bot, owner.id)

    # 1. Agar xabarni hisob egasining o'zi yozgan bo'lsa
    if sender_id == owner.id:
        # Suhbatdoshga uzoq muddatli ruxsat berish
        perm_stmt = select(ActivePermission).where(
            ActivePermission.target_type == TargetType.DM_SESSION,
            ActivePermission.owner_id == owner.id,
            ActivePermission.user_id == chat_id,
        )
        existing = (await session.execute(perm_stmt)).scalar_one_or_none()
        expires = utcnow() + timedelta(days=365)
        if existing:
            existing.expires_at = expires
        else:
            session.add(
                ActivePermission(
                    target_type=TargetType.DM_SESSION,
                    owner_id=owner.id,
                    user_id=chat_id,
                    expires_at=expires,
                )
            )
        await session.commit()
        return

    # 2. Hisob egasining DM sozlamalarini tekshirish
    inbox_stmt = select(InboxSettings).where(InboxSettings.user_id == owner.id)
    inbox = (await session.execute(inbox_stmt)).scalar_one_or_none()

    if not inbox:
        inbox = InboxSettings(
            user_id=owner.id,
            mode=InboxMode.PAID,
            price_mxtr=58823,
            session_minutes=1440,
        )
        session.add(inbox)
        await session.commit()
    elif inbox.mode == InboxMode.OPEN:
        # Hamma uchun ochiq rejimga o'tkazilgan bo'lsa
        return

    # 3. Istisnolar (Whitelist / Premium / Blacklist) tekshiruvi
    if inbox.free_for_premium and message.from_user and message.from_user.is_premium:
        return  # Telegram Premium foydalanuvchisi uchun bepul

    rule_stmt = select(AccessRule).where(
        AccessRule.owner_id == owner.id,
        AccessRule.target_id == sender_id,
    )
    rule = (await session.execute(rule_stmt)).scalar_one_or_none()
    if rule:
        if rule.kind == AccessRuleKind.FREE:
            return  # Oq ro'yxatda (istisnolarda), bepul
        if rule.kind == AccessRuleKind.BLOCKED:
            return

    # 4. Foydalanuvchining aktiv to'langan ruxsati bormi?
    now = utcnow()
    active_stmt = select(ActivePermission).where(
        ActivePermission.target_type == TargetType.DM_SESSION,
        ActivePermission.owner_id == owner.id,
        ActivePermission.user_id == sender_id,
        ActivePermission.expires_at > now,
    )
    active_perm = (await session.execute(active_stmt)).scalar_one_or_none()
    if active_perm:
        if active_perm.messages_left is not None:
            if active_perm.messages_left > 0:
                active_perm.messages_left -= 1
                await session.commit()
                return  # 1 ta xabar ruxsatidan foydalandi
        else:
            # Vaqtinchalik sessiya (24 soat yoki 30 kun)
            return

    # 5. RUXSAT YO'Q: To'lanmagan xabarni shaxsiy chatdan o'chirishga harakat qilish
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception as e:
        logger.debug("Business xabarni o'chirib bo'lmadi: %s", e)

    price_sum = int(inbox.price_mxtr / 1000 * 170) if inbox.price_mxtr else 10000
    if price_sum < 1000:
        price_sum = 10000

    unit = inbox.pricing_unit or "session"
    if unit == "per_message":
        tariff_str = "1 ta xabar uchun (Bir martalik)"
        duration_str = "1 ta xabar"
    elif unit == "monthly":
        tariff_str = "30 kunlik erkin muloqot (Oylik)"
        duration_str = "30 kun"
    else:
        hours = max(1, inbox.session_minutes // 60) if inbox.session_minutes else 24
        tariff_str = f"{hours} soatlik muloqot"
        duration_str = f"{hours} soat"

    order = await create_payment_order(
        session,
        user_id=sender_id,
        recipient_id=owner.id,
        target_type=TargetType.DM_SESSION,
        target_id=owner.id,
        amount=price_sum,
        currency="UZS",
    )
    await session.commit()

    # To'lov havolalarini tayyorlash
    click_url = get_click_url(order.id, price_sum)
    payme_url = get_payme_url(order.id, price_sum * 100)
    crypto_url = await create_cryptobot_invoice(order.id, round(price_sum / 12800, 2))

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 Muloqot huquqini sotib olish ({price_sum:,.0f} so'm)",
                    url=f"https://t.me/{settings.bot_username}?start=pay_{order.id}"
                    if settings.bot_username
                    else "https://t.me",
                )
            ]
        ]
    )
    welcome_text = inbox.welcome_text or (
        f"Salom! Men bilan bog'lanish pullik asosda tashkil etilgan.\n\n"
        f"Muloqot qilish uchun quyidagi to'lov tizimlaridan biri orqali to'lovni amalga oshiring."
    )
    text = (
        f"🔒 <b>Pullik Muloqot (DM Paywall)</b>\n\n"
        f"{welcome_text}\n\n"
        f"💰 Narxi: <b>{price_sum:,.0f} so'm</b> ({tariff_str})\n"
        f"⏱ Ruxsat: <b>{duration_str}</b>\n\n"
        f"To'lov qilganingizdan so'ng xabaringiz qabul qilinadi va egasiga yetkaziladi."
    )

    # 1-usul: Telegram Business orqali to'g'ridan-to'g'ri suhbatga yuborish
    sent = False
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML",
            business_connection_id=message.business_connection_id,
        )
        sent = True
        logger.info("Business paywall sent via business connection to chat %s", chat_id)
    except Exception as e:
        logger.warning("Business connection orqali xabar yuborilmadi (%s), botdan to'g'ridan-to'g'ri yuboriladi.", e)

    # 2-usul: Agar business connection orqali o'tmasa, botdan to'g'ridan-to'g'ri foydalanuvchiga yuborish
    if not sent and sender_id:
        try:
            await bot.send_message(
                chat_id=sender_id,
                text=f"🔒 <b>{owner.first_name or 'Foydalanuvchi'} bilan pullik muloqot</b>\n\n"
                f"Siz yuborgan xabar egasiga yetkazilishi uchun to'lovni amalga oshiring:\n\n"
                f"💰 Narxi: <b>{price_sum:,.0f} so'm</b>\n"
                f"⏱ Muloqot davomiyligi: <b>24 soat</b>",
                reply_markup=markup,
                parse_mode="HTML",
            )
            logger.info("Business paywall sent directly via bot to sender %s", sender_id)
        except Exception as e2:
            logger.warning("Botdan to'g'ridan-to'g'ri yuborishda xato: %s", e2)
