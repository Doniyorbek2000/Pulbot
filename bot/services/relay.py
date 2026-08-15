"""Xabar yetkazish (relay) xizmati.

Bot ikki foydalanuvchi o'rtasida vositachi bo'ladi:
yuboruvchi botga yozadi → pul yechiladi/ushlanadi → xabar qabul qiluvchiga
yetkaziladi → qabul qiluvchi javob berganda pul unga o'tadi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ContentKind, PricingUnit, RelayStatus, TxKind
from bot.db.models import InboxSettings, RelayMessage, RelaySession, User
from bot.i18n import Translator
from bot.keyboards.callbacks import RelayCB
from bot.services import access, app_settings, wallet
from bot.services.pricing import Quote
from bot.utils.money import format_amount, split_commission
from bot.utils.timeutils import humanize_timedelta, utcnow

logger = logging.getLogger(__name__)

PREVIEW_LENGTH = 150


class DeliveryError(Exception):
    """Xabarni yetkazib bo'lmadi (pul qaytarilgan bo'lishi kerak)."""


@dataclass(slots=True)
class DeliveryResult:
    relay: RelayMessage
    charged_mxtr: int
    held: bool
    session_started: bool = False


def detect_content_kind(message: Message) -> str:
    """Xabar turini aniqlaydi (guruhda narxlash uchun ham ishlatiladi)."""
    if message.forward_origin is not None:
        return ContentKind.FORWARD
    if message.photo:
        return ContentKind.PHOTO
    if message.video or message.video_note:
        return ContentKind.VIDEO
    if message.voice or message.audio:
        return ContentKind.VOICE
    if message.sticker:
        return ContentKind.STICKER
    if message.animation:
        return ContentKind.ANIMATION
    if message.document:
        return ContentKind.DOCUMENT
    text = message.text or message.caption or ""
    if text:
        entities = (message.entities or []) + (message.caption_entities or [])
        if any(entity.type in ("url", "text_link", "mention") for entity in entities):
            return ContentKind.LINK
        return ContentKind.TEXT
    return ContentKind.OTHER


def preview_of(message: Message) -> str:
    text = message.text or message.caption or ""
    if not text:
        return f"[{detect_content_kind(message)}]"
    text = " ".join(text.split())
    return text[:PREVIEW_LENGTH]


def recipient_keyboard(_: Translator, relay: RelayMessage) -> InlineKeyboardMarkup:
    """Qabul qiluvchi ko'radigan tugmalar."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("relay.reply_btn"),
        callback_data=RelayCB(action="reply", target_id=relay.sender_id, relay_id=relay.id),
    )
    if relay.status == RelayStatus.HELD:
        builder.button(
            text=_("relay.refund_btn"),
            callback_data=RelayCB(action="refund", target_id=relay.sender_id, relay_id=relay.id),
        )
    builder.button(
        text=_("relay.block_btn"),
        callback_data=RelayCB(action="block", target_id=relay.sender_id, relay_id=relay.id),
    )
    builder.adjust(1)
    return builder.as_markup()


def _payment_line(_: Translator, relay: RelayMessage, fmt) -> str:
    if relay.price_mxtr <= 0:
        return _("relay.new_message_free")
    if relay.status == RelayStatus.HELD:
        return _("relay.new_message_held", net=fmt(relay.net_mxtr))
    return _("relay.new_message_paid", price=fmt(relay.price_mxtr), net=fmt(relay.net_mxtr))


async def deliver(
    bot: Bot,
    session: AsyncSession,
    *,
    sender: User,
    recipient: User,
    inbox: InboxSettings,
    quote: Quote,
    message: Message,
) -> DeliveryResult:
    """Pulni yechadi va xabarni qabul qiluvchiga yetkazadi.

    Xato bo'lsa pul avtomatik qaytariladi va `DeliveryError` ko'tariladi.
    """
    commission = await app_settings.commission_bps(session)
    price = max(0, quote.price_mxtr)
    net, fee = split_commission(price, commission) if price else (0, 0)

    relay = RelayMessage(
        sender_id=sender.id,
        recipient_id=recipient.id,
        price_mxtr=price,
        net_mxtr=net,
        commission_mxtr=fee,
        status=RelayStatus.FREE,
        source_message_id=message.message_id,
        preview=preview_of(message),
        content_kind=detect_content_kind(message),
    )
    session.add(relay)
    await session.flush()

    session_started = False
    held = False

    if price > 0:
        # Pul yuboruvchidan yechiladi (escrow yoki to'g'ridan-to'g'ri)
        await wallet.hold(
            session,
            sender.id,
            price,
            ref_type="relay",
            ref_id=relay.id,
            counterparty_id=recipient.id,
        )
        if inbox.hold_hours > 0:
            relay.status = RelayStatus.HELD
            relay.hold_until = utcnow() + timedelta(hours=inbox.hold_hours)
            held = True
        else:
            await _settle(session, relay, commission)

        # Sessiya rejimi: to'lov sessiyani ochadi
        if inbox.pricing_unit == PricingUnit.PER_SESSION and quote.session_id is None:
            minutes = max(1, inbox.session_minutes)
            relay_session = RelaySession(
                sender_id=sender.id,
                recipient_id=recipient.id,
                paid_mxtr=price,
                expires_at=utcnow() + timedelta(minutes=minutes),
                messages_sent=1,
            )
            session.add(relay_session)
            await session.flush()
            relay.session_id = relay_session.id
            session_started = True
    elif quote.session_id:
        relay.session_id = quote.session_id
        existing = await session.get(RelaySession, quote.session_id)
        if existing is not None:
            existing.messages_sent += 1

    await session.flush()

    # --- Yetkazish ---
    try:
        await _send_to_recipient(bot, session, relay, sender, recipient, message)
    except TelegramForbiddenError as exc:
        logger.info("Qabul qiluvchi %s botni bloklagan: %s", recipient.id, exc)
        await _rollback_delivery(session, relay)
        recipient.bot_blocked = True
        await session.flush()
        raise DeliveryError("recipient_unreachable") from exc
    except TelegramAPIError as exc:
        logger.warning("Xabar yetkazilmadi (%s): %s", recipient.id, exc)
        await _rollback_delivery(session, relay)
        raise DeliveryError("delivery_failed") from exc

    await access.bump_inbox_usage(
        session,
        recipient.id,
        sender.id,
        recipient.tz_offset_minutes,
        earned_mxtr=net if not held else 0,
    )
    return DeliveryResult(
        relay=relay, charged_mxtr=price, held=held, session_started=session_started
    )


async def _send_to_recipient(
    bot: Bot,
    session: AsyncSession,
    relay: RelayMessage,
    sender: User,
    recipient: User,
    message: Message,
) -> None:
    translator = Translator(recipient.language)
    rate_uzs, rate_usd = await app_settings.rates(session)

    def fmt(amount: int) -> str:
        return format_amount(amount, recipient.display_currency, rate_uzs=rate_uzs, rate_usd=rate_usd)

    payment_line = _payment_line(translator, relay, fmt)
    sender_label = f"{sender.full_name}" + (f" (@{sender.username})" if sender.username else "")
    keyboard = recipient_keyboard(translator, relay)

    is_plain_text = bool(message.text) and not message.entities
    if is_plain_text:
        header = await bot.send_message(
            recipient.id,
            translator(
                "relay.new_message",
                sender=sender_label,
                payment_line=payment_line,
                text=message.html_text if message.text else "",
            ),
            reply_markup=keyboard,
        )
    else:
        # Media yoki formatlangan matn — asl ko'rinishida nusxalanadi
        copied = await bot.copy_message(
            chat_id=recipient.id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        header = await bot.send_message(
            recipient.id,
            translator(
                "relay.new_message",
                sender=sender_label,
                payment_line=payment_line,
                text="",
            ).rstrip(),
            reply_markup=keyboard,
            reply_to_message_id=copied.message_id,
        )

    relay.delivered_message_id = header.message_id
    await session.flush()


async def _rollback_delivery(session: AsyncSession, relay: RelayMessage) -> None:
    """Yetkazib bo'lmaganda pulni qaytaradi."""
    if relay.price_mxtr > 0 and relay.status in (RelayStatus.HELD, RelayStatus.RELEASED):
        if relay.status == RelayStatus.RELEASED:
            # Pul allaqachon qabul qiluvchiga o'tgan — uni qaytarib olamiz
            try:
                await wallet.debit(
                    session,
                    relay.recipient_id,
                    relay.net_mxtr,
                    TxKind.MESSAGE_REFUND,
                    allow_locked=True,
                    ref_type="relay_rollback",
                    ref_id=relay.id,
                    idempotency_key=f"rollback:relay:{relay.id}",
                )
            except wallet.InsufficientFunds:
                logger.error("Rollback: %s hisobida mablag' yo'q", relay.recipient_id)
        await wallet.refund(
            session,
            relay.sender_id,
            relay.price_mxtr,
            ref_type="relay",
            ref_id=relay.id,
            note="Yetkazilmadi",
        )
    relay.status = RelayStatus.REFUNDED
    relay.settled_at = utcnow()
    await session.flush()


async def _settle(session: AsyncSession, relay: RelayMessage, commission_bps: int) -> None:
    """Escrow'dagi pulni qabul qiluvchiga o'tkazadi."""
    if relay.status in (RelayStatus.RELEASED, RelayStatus.REFUNDED, RelayStatus.REJECTED):
        return
    if relay.price_mxtr > 0:
        result = await wallet.release_to(
            session,
            relay.recipient_id,
            relay.price_mxtr,
            commission_bps,
            kind=TxKind.MESSAGE_EARN,
            ref_type="relay",
            ref_id=relay.id,
            counterparty_id=relay.sender_id,
        )
        relay.net_mxtr = result.net_mxtr
        relay.commission_mxtr = result.commission_mxtr
    relay.status = RelayStatus.RELEASED
    relay.settled_at = utcnow()
    await session.flush()


async def settle_on_reply(session: AsyncSession, relay: RelayMessage) -> int:
    """Qabul qiluvchi javob berdi — escrow yopiladi. Qaytadi: o'tgan summa."""
    if relay.status != RelayStatus.HELD:
        return 0
    commission = await app_settings.commission_bps(session)
    await _settle(session, relay, commission)
    return relay.net_mxtr


async def reject(session: AsyncSession, relay: RelayMessage) -> int:
    """Qabul qiluvchi rad etdi — pul yuboruvchiga qaytadi."""
    if relay.status != RelayStatus.HELD:
        return 0
    await wallet.refund(
        session,
        relay.sender_id,
        relay.price_mxtr,
        ref_type="relay",
        ref_id=relay.id,
        note="Rad etildi",
    )
    relay.status = RelayStatus.REJECTED
    relay.settled_at = utcnow()
    await session.flush()
    return relay.price_mxtr


async def find_by_delivered_message(
    session: AsyncSession, recipient_id: int, message_id: int
) -> RelayMessage | None:
    """Qabul qiluvchi header xabariga reply qilganda topish uchun."""
    stmt = (
        select(RelayMessage)
        .where(
            RelayMessage.recipient_id == recipient_id,
            RelayMessage.delivered_message_id == message_id,
        )
        .order_by(RelayMessage.id.desc())
    )
    return (await session.execute(stmt)).scalars().first()


async def send_reply(
    bot: Bot,
    session: AsyncSession,
    *,
    owner: User,
    target: User,
    message: Message,
    relay: RelayMessage | None = None,
) -> RelayMessage:
    """Egasining javobini yuboruvchiga yetkazadi (bepul)."""
    reply_relay = RelayMessage(
        sender_id=owner.id,
        recipient_id=target.id,
        price_mxtr=0,
        status=RelayStatus.FREE,
        source_message_id=message.message_id,
        preview=preview_of(message),
        content_kind=detect_content_kind(message),
        is_reply_from_owner=True,
        reply_to_relay_id=relay.id if relay else None,
    )
    session.add(reply_relay)
    await session.flush()

    translator = Translator(target.language)
    owner_label = owner.full_name + (f" (@{owner.username})" if owner.username else "")

    builder = InlineKeyboardBuilder()
    builder.button(
        text=translator("relay.reply_btn"),
        callback_data=RelayCB(action="write", target_id=owner.id),
    )

    if message.text and not message.entities:
        sent = await bot.send_message(
            target.id,
            translator(
                "relay.new_message",
                sender=owner_label,
                payment_line=translator("relay.new_message_free"),
                text=message.html_text,
            ),
            reply_markup=builder.as_markup(),
        )
    else:
        copied = await bot.copy_message(
            chat_id=target.id, from_chat_id=message.chat.id, message_id=message.message_id
        )
        sent = await bot.send_message(
            target.id,
            translator(
                "relay.new_message", sender=owner_label,
                payment_line=translator("relay.new_message_free"), text=""
            ).rstrip(),
            reply_markup=builder.as_markup(),
            reply_to_message_id=copied.message_id,
        )

    reply_relay.delivered_message_id = sent.message_id
    await session.flush()
    return reply_relay


async def expire_holds(bot: Bot, session: AsyncSession, *, limit: int = 200) -> tuple[int, int]:
    """Muddati o'tgan escrow'larni yakunlaydi (rejalashtirilgan vazifa).

    Qaytaradi: (o'tkazilgan, qaytarilgan) soni.
    """
    now = utcnow()
    stmt = (
        select(RelayMessage)
        .where(RelayMessage.status == RelayStatus.HELD, RelayMessage.hold_until <= now)
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return 0, 0

    commission = await app_settings.commission_bps(session)
    released = refunded = 0

    for relay in rows:
        inbox = await session.get(InboxSettings, relay.recipient_id)
        should_refund = bool(inbox and inbox.refund_if_no_reply)

        if should_refund:
            await wallet.refund(
                session,
                relay.sender_id,
                relay.price_mxtr,
                ref_type="relay",
                ref_id=relay.id,
                note="Javob berilmadi",
            )
            relay.status = RelayStatus.REFUNDED
            relay.settled_at = now
            refunded += 1
            await _notify_expiry(bot, session, relay, refunded=True)
        else:
            await _settle(session, relay, commission)
            released += 1
            await _notify_expiry(bot, session, relay, refunded=False)

    await session.flush()
    return released, refunded


async def _notify_expiry(
    bot: Bot, session: AsyncSession, relay: RelayMessage, *, refunded: bool
) -> None:
    rate_uzs, rate_usd = await app_settings.rates(session)
    try:
        if refunded:
            sender = await session.get(User, relay.sender_id)
            recipient = await session.get(User, relay.recipient_id)
            if sender is None or recipient is None:
                return
            translator = Translator(sender.language)
            inbox = await session.get(InboxSettings, relay.recipient_id)
            await bot.send_message(
                sender.id,
                translator(
                    "relay.auto_refunded",
                    name=recipient.full_name,
                    hours=inbox.hold_hours if inbox else 0,
                    price=format_amount(
                        relay.price_mxtr, sender.display_currency,
                        rate_uzs=rate_uzs, rate_usd=rate_usd,
                    ),
                ),
            )
        else:
            recipient = await session.get(User, relay.recipient_id)
            if recipient is None:
                return
            translator = Translator(recipient.language)
            await bot.send_message(
                recipient.id,
                translator(
                    "relay.auto_released",
                    amount=format_amount(
                        relay.net_mxtr, recipient.display_currency,
                        rate_uzs=rate_uzs, rate_usd=rate_usd,
                    ),
                ),
            )
    except TelegramAPIError as exc:
        logger.debug("Escrow bildirishnomasi yuborilmadi: %s", exc)


async def close_expired_sessions(session: AsyncSession) -> int:
    """Muddati tugagan sessiyalarni yopadi."""
    stmt = select(RelaySession).where(
        RelaySession.active.is_(True), RelaySession.expires_at <= utcnow()
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for row in rows:
        row.active = False
    await session.flush()
    return len(rows)


def session_time_left(relay_session: RelaySession, lang: str) -> str:
    return humanize_timedelta(relay_session.expires_at - utcnow(), lang)
