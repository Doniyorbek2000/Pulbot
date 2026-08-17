"""To'lovlar tasdiqlangach buyurtmalarni bajarish (Fulfillment) xizmati.

Barcha to'lov shlyuzlari (Click, Payme, CryptoBot, Stars) to'lov muvaffaqiyatli
bo'lganda aynan shu yagona markaziy xizmatni chaqiradi.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.types import ChatPermissions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import PaymentStatus, TargetType, TxKind
from bot.db.models import (
    ActivePermission,
    ChatSettings,
    InboxSettings,
    PaymentOrder,
    Subscription,
    User,
)
from bot.i18n import get
from bot.services import app_settings, wallet
from bot.utils.money import format_amount, split_commission, stars_to_mxtr
from bot.utils.timeutils import utcnow

logger = logging.getLogger(__name__)


async def fulfill_order(bot: Bot, session: AsyncSession, order: PaymentOrder) -> bool:
    """Buyurtmani to'liq bajaradi va barcha kerakli huquqlarni ochadi."""
    if order.status == PaymentStatus.PAID and order.completed_at is not None:
        logger.info("Order %s allaqachon bajarilgan.", order.id)
        return True

    order.status = PaymentStatus.PAID
    order.completed_at = utcnow()
    await session.flush()

    platform_commission_bps = int(await app_settings.get(session, "commission_bps", settings.commission_bps))

    try:
        if order.target_type == TargetType.DM_SESSION:
            await _fulfill_dm_session(bot, session, order, platform_commission_bps)
        elif order.target_type == TargetType.GROUP_CHAT:
            await _fulfill_group_chat(bot, session, order, platform_commission_bps)
        elif order.target_type == TargetType.CHANNEL_SUB:
            await _fulfill_channel_subscription(bot, session, order, platform_commission_bps)
        elif order.target_type == TargetType.TOPUP:
            await _fulfill_topup(session, order)
        else:
            logger.warning("Noma'lum target_type: %s (Order: %s)", order.target_type, order.id)

        await session.commit()
        return True
    except Exception:
        logger.exception("Order %s ni bajarishda xatolik yuz berdi", order.id)
        await session.rollback()
        return False


async def _fulfill_dm_session(
    bot: Bot, session: AsyncSession, order: PaymentOrder, commission_bps: int
) -> None:
    """Shaxsiy chat (DM) muloqot huquqini berish."""
    owner_id = order.recipient_id or order.target_id
    customer_id = order.user_id

    # Inbox sozlamalarini olish
    inbox = (
        await session.execute(select(InboxSettings).where(InboxSettings.user_id == owner_id))
    ).scalar_one_or_none()

    unit = inbox.pricing_unit if inbox and inbox.pricing_unit else "session"
    is_per_msg = unit == "per_message"
    
    if unit == "monthly":
        duration_hours = 720
    else:
        duration_hours = inbox.session_minutes // 60 if inbox and inbox.session_minutes else 24
        if duration_hours < 1:
            duration_hours = 24

    expires_at = utcnow() + timedelta(days=365 if is_per_msg else (duration_hours / 24))

    # Mavjud ruxsatni tekshirish va yangilash
    stmt = select(ActivePermission).where(
        ActivePermission.target_type == TargetType.DM_SESSION,
        ActivePermission.owner_id == owner_id,
        ActivePermission.user_id == customer_id,
    )
    perm = (await session.execute(stmt)).scalar_one_or_none()
    if perm:
        perm.expires_at = expires_at
        if is_per_msg:
            perm.messages_left = (perm.messages_left or 0) + 1
        else:
            perm.messages_left = None
    else:
        perm = ActivePermission(
            target_type=TargetType.DM_SESSION,
            owner_id=owner_id,
            user_id=customer_id,
            expires_at=expires_at,
            messages_left=1 if is_per_msg else None,
        )
        session.add(perm)

    # Tushumni hisob egasining hamyoniga o'tkazish (agar mXTR ga bog'liq bo'lsa)
    amount_mxtr = order.amount if order.currency == "XTR" else order.amount // 170 * 1000
    if amount_mxtr > 0:
        net, comm = split_commission(amount_mxtr, commission_bps)
        await wallet.credit(
            session,
            owner_id,
            net,
            kind=TxKind.MESSAGE_EARN,
            counterparty_id=customer_id,
            ref_type="order",
            ref_id=order.id,
            note="DM pullik muloqot to'lovi",
            idempotency_key=f"order_credit:{order.id}",
        )

    # Foydalanuvchilar tilini bilish
    buyer = (await session.execute(select(User).where(User.id == customer_id))).scalar_one_or_none()
    owner = (await session.execute(select(User).where(User.id == owner_id))).scalar_one_or_none()

    tariff_notify = "1 ta xabar yuborish huquqi" if is_per_msg else f"{duration_hours} soat davomida erkin muloqot qilish huquqi"

    # Mijozga bildirishnoma
    try:
        await bot.send_message(
            chat_id=customer_id,
            text=f"✅ <b>To'lov muvaffaqiyatli qabul qilindi!</b>\n\n"
            f"Sizga {owner.mention if owner else 'foydalanuvchi'} bilan {tariff_notify} ochildi.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug("Mijozga xabar yuborib bo'lmadi: %s", e)

    # Hisob egasiga bildirishnoma
    try:
        await bot.send_message(
            chat_id=owner_id,
            text=f"💰 <b>Sizga yangi pullik muloqot to'lovi tushdi!</b>\n\n"
            f"Foydalanuvchi: {buyer.mention if buyer else customer_id}\n"
            f"Summa: <b>{order.amount:,.0f} {order.currency}</b>\n"
            f"Ruxsat muddati: <b>{duration_hours} soat</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug("Egaga xabar yuborib bo'lmadi: %s", e)


async def _fulfill_group_chat(
    bot: Bot, session: AsyncSession, order: PaymentOrder, commission_bps: int
) -> None:
    """Guruhda yozish huquqini berish (Pay-to-Chat)."""
    chat_id = order.target_id
    user_id = order.user_id

    chat_row = (
        await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
    ).scalar_one_or_none()
    duration_days = 30
    expires_at = utcnow() + timedelta(days=duration_days)

    # Ruxsatni saqlash
    stmt = select(ActivePermission).where(
        ActivePermission.target_type == TargetType.GROUP_CHAT,
        ActivePermission.owner_id == chat_id,
        ActivePermission.user_id == user_id,
    )
    perm = (await session.execute(stmt)).scalar_one_or_none()
    if perm:
        perm.expires_at = expires_at
    else:
        perm = ActivePermission(
            target_type=TargetType.GROUP_CHAT,
            owner_id=chat_id,
            user_id=user_id,
            expires_at=expires_at,
        )
        session.add(perm)

    # Guruhda Telegram permissionlarini ochish
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
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
            ),
            use_independent_chat_permissions=True,
        )
    except Exception as e:
        logger.warning("Guruh a'zosi huquqini ochishda xato: %s", e)

    # Guruh egasiga tushum
    if chat_row and chat_row.owner_id:
        amount_mxtr = order.amount if order.currency == "XTR" else order.amount // 170 * 1000
        if amount_mxtr > 0:
            net, _ = split_commission(amount_mxtr, commission_bps)
            await wallet.credit(
                session,
                chat_row.owner_id,
                net,
                kind=TxKind.CHAT_EARN,
                chat_id=chat_id,
                counterparty_id=user_id,
                ref_type="order",
                ref_id=order.id,
                note="Guruhda yozish to'lovi",
                idempotency_key=f"group_credit:{order.id}",
            )

    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ <b>Guruhda yozish huquqi faollashtirildi!</b>\n\n"
            f"Guruh: <b>{chat_row.title if chat_row else chat_id}</b>\n"
            f"Amal qilish muddati: <b>{duration_days} kun</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug("Foydalanuvchiga guruh to'lov xabari yetmadi: %s", e)


async def _fulfill_channel_subscription(
    bot: Bot, session: AsyncSession, order: PaymentOrder, commission_bps: int
) -> None:
    """Kanal yoki VIP guruh obunasini yaratish va 1 martalik link berish."""
    chat_id = order.target_id
    user_id = order.user_id

    chat_row = (
        await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
    ).scalar_one_or_none()
    duration_days = 30
    expires_at = utcnow() + timedelta(days=duration_days)

    # 1 martalik taklif havolasi yaratish (1 kishi uchun, 3 kun amal qiladi)
    invite_expire = utcnow() + timedelta(days=3)
    invite_link = None
    try:
        link_obj = await bot.create_chat_invite_link(
            chat_id=chat_id,
            name=f"Sub:{user_id}",
            member_limit=1,
            expire_date=invite_expire,
        )
        invite_link = link_obj.invite_link
    except Exception as e:
        logger.warning("Invite link yaratishda xatolik: %s", e)

    sub = Subscription(
        chat_id=chat_id,
        user_id=user_id,
        invite_link=invite_link,
        is_active=True,
        warned_expiration=False,
        expires_at=expires_at,
    )
    session.add(sub)

    # Kanal egasiga pul tushirish
    if chat_row and chat_row.owner_id:
        amount_mxtr = order.amount if order.currency == "XTR" else order.amount // 170 * 1000
        if amount_mxtr > 0:
            net, _ = split_commission(amount_mxtr, commission_bps)
            await wallet.credit(
                session,
                chat_row.owner_id,
                net,
                kind=TxKind.CHAT_EARN,
                chat_id=chat_id,
                counterparty_id=user_id,
                ref_type="order",
                ref_id=order.id,
                note="Kanal obunasi to'lovi",
                idempotency_key=f"sub_credit:{order.id}",
            )

    try:
        link_text = (
            f"\n\n🔗 <b>Sizning shaxsiy 1 martalik kirish havolangiz:</b>\n{invite_link}"
            if invite_link
            else ""
        )
        await bot.send_message(
            chat_id=user_id,
            text=f"🎉 <b>Obuna muvaffaqiyatli rasmiylashtirildi!</b>\n\n"
            f"Kanal/Guruh: <b>{chat_row.title if chat_row else chat_id}</b>\n"
            f"Obuna muddati: <b>{duration_days} kun</b> (tugash vaqti: {expires_at.strftime('%Y-%m-%d %H:%M')})"
            f"{link_text}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug("Mijozga havola yetkazilmadi: %s", e)


async def _fulfill_topup(session: AsyncSession, order: PaymentOrder) -> None:
    """Foydalanuvchining o'z hisobini to'ldirish."""
    amount_mxtr = order.amount if order.currency == "XTR" else order.amount // 170 * 1000
    await wallet.credit(
        session,
        order.user_id,
        amount_mxtr,
        kind=TxKind.TOPUP,
        ref_type="order",
        ref_id=order.id,
        note=f"{order.provider.upper()} orqali to'ldirish",
        idempotency_key=f"topup_order:{order.id}",
    )
