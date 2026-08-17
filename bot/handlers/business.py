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
    
    # Agar connection_id DBda saqlanmagan bo'lsa, chat_id yoki birinchi admin orqali bog'lash
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

    # 3. Istisnolar (Whitelist / Blacklist) tekshiruvi
    rule_stmt = select(AccessRule).where(
        AccessRule.owner_id == owner.id,
        AccessRule.target_id == sender_id,
    )
    rule = (await session.execute(rule_stmt)).scalar_one_or_none()
    if rule:
        if rule.kind == AccessRuleKind.FREE:
            return  # Oq ro'yxatda, bepul
        if rule.kind == AccessRuleKind.BLOCKED:
            # Bloklangan, xabarni e'tiborsiz qoldirish
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
        # Ruxsat mavjud, xabar erkin o'tadi
        return

    # 5. RUXSAT YO'Q: To'lov talab qilish (Paywall)
    price_sum = int(inbox.price_mxtr / 1000 * 170) if inbox.price_mxtr else 10000
    if price_sum < 1000:
        price_sum = 10000

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

    buttons = [
        [
            InlineKeyboardButton(text="🔹 Click orqali to'lash", url=click_url),
            InlineKeyboardButton(text="🟢 Payme orqali to'lash", url=payme_url),
        ]
    ]
    if crypto_url:
        buttons.append([InlineKeyboardButton(text="💎 USDT / TON (@CryptoBot)", url=crypto_url)])

    if settings.bot_username:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="⭐ Telegram Stars / Balansdan",
                    url=f"https://t.me/{settings.bot_username}?start=pay_{order.id}",
                )
            ]
        )

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    welcome_text = inbox.welcome_text or (
        f"Salom! Men bilan bog'lanish pullik asosda tashkil etilgan.\n\n"
        f"Muloqot qilish uchun quyidagi to'lov tizimlaridan biri orqali to'lovni amalga oshiring."
    )
    text = (
        f"🔒 <b>Pullik Muloqot (DM Paywall)</b>\n\n"
        f"{welcome_text}\n\n"
        f"💰 Narxi: <b>{price_sum:,.0f} so'm</b>\n"
        f"⏱ Muloqot davomiyligi: <b>24 soat</b>\n\n"
        f"To'lov qilganingizdan so'ng xabaringiz qabul qilinadi va egasiga yetkaziladi."
    )

    try:
        # Xabarni to'g'ridan-to'g'ri suhbatga (business connection orqali) javob qilib yuboramiz
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML",
            business_connection_id=message.business_connection_id,
        )
        logger.info("Business paywall reply sent to chat_id=%s", chat_id)
    except Exception as e:
        logger.warning("Business paywall xabarini yuborishda xatolik: %s", e)
