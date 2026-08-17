"""Bot orqali yozish: narxni ko'rsatish, to'lov, yetkazish, javob berish."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import AccessRuleKind, PricingUnit, RelayStatus
from bot.db.models import RelayMessage, RelaySession, User
from bot.handlers.common import notify, render_main_menu, safe_edit
from bot.i18n import Translator
from bot.keyboards.callbacks import MenuCB, RelayCB, WalletCB
from bot.keyboards.menus import back_to
from bot.services import access, app_settings, pricing, relay as relay_service, users, wallet
from bot.services.pricing import Reason
from bot.states import RelaySG
from bot.utils.money import format_amount
from bot.utils.timeutils import humanize_timedelta, utcnow

logger = logging.getLogger(__name__)

router = Router(name="relay")

#: Ruxsat berilmagan sabablar uchun matn kalitlari
DENY_TEXT = {
    Reason.RULE_BLOCKED: "relay.blocked",
    Reason.CLOSED: "relay.closed",
    Reason.NOT_PREMIUM: "relay.not_premium",
    Reason.LIMIT_REACHED: "relay.limit_reached",
    Reason.SENDER_LIMIT: "relay.sender_limit",
    Reason.SCHEDULE_CLOSED: "relay.schedule_closed",
    Reason.BANNED: "error.banned",
}


# --------------------------------------------------------------------------
# Yozish ekrani
# --------------------------------------------------------------------------


async def open_compose(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    sender: User,
    target: User,
    _: Translator,
    fmt,
    *,
    edit: bool = False,
) -> None:
    """Kimgadir yozish ekranini ochadi: narx, sabab va tugmalar."""
    message = event.message if isinstance(event, CallbackQuery) else event

    if target.id == sender.id:
        await message.answer(_("relay.self"))
        return

    inbox = await users.get_inbox(session, target.id)
    quote = await pricing.quote_dm(session, sender, target, inbox)

    if not quote.allowed:
        key = DENY_TEXT.get(quote.reason, "relay.closed")
        await message.answer(
            _(key, name=target.full_name, reason=_(f"reason.{quote.reason}")),
            reply_markup=back_to(_, "home"),
        )
        return

    balance_mxtr, available_mxtr = await wallet.balance(session, sender.id)
    welcome = inbox.welcome_text or ""
    lines = [_("relay.intro", name=target.full_name, welcome=welcome).strip()]

    builder = InlineKeyboardBuilder()

    if quote.is_free:
        if quote.reason == Reason.SESSION_ACTIVE and quote.session_id:
            existing = await session.get(RelaySession, quote.session_id)
            left = (
                humanize_timedelta(existing.expires_at - utcnow(), _.lang)
                if existing
                else "—"
            )
            lines.append(_("relay.session_active", left=left))
        else:
            lines.append(_("relay.free_notice", reason=_(f"reason.{quote.reason}")))
        builder.button(
            text=_("relay.reply_btn"), callback_data=RelayCB(action="write", target_id=target.id)
        )
    else:
        if quote.unit == PricingUnit.PER_SESSION:
            lines.append(
                _(
                    "relay.session_notice",
                    price=fmt(quote.price_mxtr),
                    minutes=quote.session_minutes,
                    balance=fmt(available_mxtr),
                )
            )
        else:
            lines.append(
                _("relay.price_notice", price=fmt(quote.price_mxtr), balance=fmt(available_mxtr))
            )

        if available_mxtr >= quote.price_mxtr:
            builder.button(
                text=_("relay.pay_and_send"),
                callback_data=RelayCB(action="write", target_id=target.id),
            )
        else:
            lines.append("")
            lines.append(
                _(
                    "relay.insufficient",
                    needed=fmt(quote.price_mxtr),
                    available=fmt(available_mxtr),
                    missing=fmt(quote.price_mxtr - available_mxtr),
                )
            )
            builder.button(text=_("relay.topup_now"), callback_data=WalletCB(action="topup"))

    builder.button(text=_("common.back"), callback_data=MenuCB(action="home"))
    builder.adjust(1)

    await state.update_data(target_id=target.id, quoted_price=quote.price_mxtr)

    text = "\n\n".join(part for part in lines if part)
    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(RelayCB.filter(F.action == "write"))
async def start_writing(
    query: CallbackQuery,
    callback_data: RelayCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    target = await session.get(User, callback_data.target_id)
    if target is None:
        await query.answer(_("relay.not_found"), show_alert=True)
        return

    inbox = await users.get_inbox(session, target.id)
    quote = await pricing.quote_dm(session, user, target, inbox)
    if not quote.allowed:
        await query.answer(_(f"reason.{quote.reason}"), show_alert=True)
        return

    await state.set_state(RelaySG.writing)
    await state.update_data(target_id=target.id, quoted_price=quote.price_mxtr)

    builder = InlineKeyboardBuilder()
    builder.button(text=_("common.cancel"), callback_data=MenuCB(action="home"))

    await safe_edit(query, _("relay.write_prompt"), builder.as_markup())
    await query.answer()


@router.message(RelaySG.writing)
async def handle_outgoing(
    message: Message,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    """Foydalanuvchi yozgan xabarni to'lov bilan yetkazadi."""
    data = await state.get_data()
    target_id = data.get("target_id")
    quoted_price = int(data.get("quoted_price", 0))

    target = await session.get(User, target_id) if target_id else None
    if target is None:
        await state.clear()
        await message.answer(_("relay.not_found"))
        return

    inbox = await users.get_inbox(session, target.id)
    quote = await pricing.quote_dm(session, user, target, inbox)

    if not quote.allowed:
        await state.clear()
        key = DENY_TEXT.get(quote.reason, "relay.closed")
        await message.answer(_(key, name=target.full_name, reason=_(f"reason.{quote.reason}")))
        return

    # Narx yozayotgan vaqtda oshgan bo'lsa — qayta tasdiqlashni so'raymiz
    if quote.price_mxtr > quoted_price:
        await state.clear()
        await message.answer(_("relay.price_notice", price=fmt(quote.price_mxtr), balance=""))
        await open_compose(message, state, session, user, target, _, fmt)
        return

    _balance, available = await wallet.balance(session, user.id)
    if quote.price_mxtr > available:
        await state.clear()
        builder = InlineKeyboardBuilder()
        builder.button(text=_("relay.topup_now"), callback_data=WalletCB(action="topup"))
        builder.adjust(1)
        await message.answer(
            _(
                "relay.insufficient",
                needed=fmt(quote.price_mxtr),
                available=fmt(available),
                missing=fmt(quote.price_mxtr - available),
            ),
            reply_markup=builder.as_markup(),
        )
        return

    try:
        result = await relay_service.deliver(
            bot, session,
            sender=user, recipient=target, inbox=inbox, quote=quote, message=message,
        )
    except relay_service.DeliveryError as exc:
        await state.clear()
        key = "relay.recipient_unreachable" if str(exc) == "recipient_unreachable" else "error.generic"
        await message.answer(_(key))
        return
    except wallet.InsufficientFunds:
        await state.clear()
        await message.answer(_("error.generic"))
        return

    # Sessiya rejimida foydalanuvchi yozishda davom etadi
    if inbox.pricing_unit == PricingUnit.PER_SESSION and (
        result.session_started or quote.reason == Reason.SESSION_ACTIVE
    ):
        if result.session_started:
            await message.answer(_("relay.session_started", minutes=inbox.session_minutes))
    else:
        await state.clear()

    if result.charged_mxtr <= 0:
        await message.answer(_("relay.sent_free"))
    else:
        payment_line = (
            _("relay.sent_paid", price=fmt(result.charged_mxtr), hours=inbox.hold_hours)
            if result.held
            else _("relay.sent_paid_direct", price=fmt(result.charged_mxtr))
        )
        await message.answer(_("relay.sent", payment_line=payment_line))

    if await state.get_state() is None:
        await render_main_menu(message, session, user, _, fmt, edit=False)


# --------------------------------------------------------------------------
# Qabul qiluvchi tomoni: javob, bloklash, rad etish
# --------------------------------------------------------------------------


@router.callback_query(RelayCB.filter(F.action == "reply"))
async def start_reply(
    query: CallbackQuery,
    callback_data: RelayCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    target = await session.get(User, callback_data.target_id)
    if target is None:
        await query.answer(_("relay.not_found"), show_alert=True)
        return

    await state.set_state(RelaySG.replying)
    await state.update_data(reply_to=target.id, relay_id=callback_data.relay_id)

    builder = InlineKeyboardBuilder()
    builder.button(text=_("common.cancel"), callback_data=MenuCB(action="home"))
    await query.message.answer(
        _("relay.reply_prompt", name=target.full_name), reply_markup=builder.as_markup()
    )
    await query.answer()


@router.message(RelaySG.replying)
async def handle_reply(
    message: Message,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    data = await state.get_data()
    target = await session.get(User, data.get("reply_to", 0))
    if target is None:
        await state.clear()
        await message.answer(_("relay.not_found"))
        return

    relay = await session.get(RelayMessage, data.get("relay_id", 0)) if data.get("relay_id") else None
    await state.clear()

    try:
        await relay_service.send_reply(
            bot, session, owner=user, target=target, message=message, relay=relay
        )
    except Exception as exc:  # noqa: BLE001 — foydalanuvchiga tushunarli xabar
        logger.warning("Javob yuborilmadi: %s", exc)
        await message.answer(_("error.user_blocked_bot"))
        return

    await message.answer(_("relay.reply_sent"))

    # Escrow'ni yopamiz — javob berildi
    if relay is not None and relay.recipient_id == user.id and relay.status == RelayStatus.HELD:
        earned = await relay_service.settle_on_reply(session, relay)
        if earned:
            await message.answer(_("relay.reply_earned", net=fmt(earned)))


@router.message(F.chat.type == "private", F.reply_to_message)
async def reply_by_quoting(
    message: Message,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    """Yetkazilgan xabarga oddiy reply qilish orqali javob berish."""
    relay = await relay_service.find_by_delivered_message(
        session, user.id, message.reply_to_message.message_id
    )
    if relay is None:
        return

    target = await session.get(User, relay.sender_id)
    if target is None:
        return

    try:
        await relay_service.send_reply(
            bot, session, owner=user, target=target, message=message, relay=relay
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reply-javob yuborilmadi: %s", exc)
        await message.answer(_("error.user_blocked_bot"))
        return

    await message.answer(_("relay.reply_sent"))
    if relay.status == RelayStatus.HELD:
        earned = await relay_service.settle_on_reply(session, relay)
        if earned:
            await message.answer(_("relay.reply_earned", net=fmt(earned)))


@router.callback_query(RelayCB.filter(F.action == "block"))
async def block_sender(
    query: CallbackQuery,
    callback_data: RelayCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    await access.set_rule(
        session, callback_data.target_id, AccessRuleKind.BLOCKED, owner_id=user.id
    )
    await query.answer(_("relay.blocked_done"), show_alert=True)

    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(RelayCB.filter(F.action == "refund"))
async def refund_message(
    query: CallbackQuery,
    callback_data: RelayCB,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    relay = await session.get(RelayMessage, callback_data.relay_id)
    if relay is None or relay.recipient_id != user.id:
        await query.answer(_("error.not_found"), show_alert=True)
        return
    if relay.status != RelayStatus.HELD:
        await query.answer(_("error.expired"), show_alert=True)
        return

    refunded = await relay_service.reject(session, relay)
    await query.answer()
    await safe_edit(query, _("relay.refunded_done", price=fmt(refunded)))

    sender = await session.get(User, relay.sender_id)
    if sender is not None:
        rate_uzs, rate_usd = await app_settings.rates(session)
        sender_translator = Translator(sender.language)
        await notify(
            bot,
            sender.id,
            sender_translator(
                "relay.refund_notice",
                name=user.full_name,
                price=format_amount(
                    refunded, sender.display_currency, rate_uzs=rate_uzs, rate_usd=rate_usd
                ),
            ),
        )


@router.callback_query(RelayCB.filter(F.action == "profile"))
async def show_profile(
    query: CallbackQuery,
    callback_data: RelayCB,
    session: AsyncSession,
    _: Translator,
    fmt,
) -> None:
    from sqlalchemy import func, select

    target = await session.get(User, callback_data.target_id)
    if target is None:
        await query.answer(_("error.not_found"), show_alert=True)
        return

    received = int(
        (
            await session.execute(
                select(func.count(RelayMessage.id)).where(RelayMessage.recipient_id == target.id)
            )
        ).scalar_one()
    )
    target_wallet = await wallet.get_wallet(session, target.id)

    await query.answer()
    await query.message.answer(
        _(
            "profile.title",
            name=target.full_name,
            id=target.id,
            username=f"@{target.username}" if target.username else "",
            premium="✅" if target.is_premium else "❌",
            joined=target.created_at.strftime("%d.%m.%Y"),
            received=received,
            earned=fmt(target_wallet.total_earned_mxtr),
        )
    )
