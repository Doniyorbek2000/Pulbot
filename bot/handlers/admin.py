"""Admin panel: statistika, foydalanuvchilar, pul yechish, kurslar, tarqatish."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import PaymentStatus, TxKind, WithdrawStatus
from bot.db.models import (
    AuditLog,
    ChatSettings,
    Payment,
    RelayMessage,
    User,
    Wallet,
    Withdrawal,
)
from bot.handlers.common import make_fmt, notify, parse_amount, safe_edit
from bot.i18n import Translator
from bot.keyboards.callbacks import AdminCB, MenuCB
from bot.services import app_settings, users, wallet, withdrawals
from bot.states import AdminSG
from bot.utils.money import stars_to_mxtr
from bot.utils.timeutils import utcnow

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _guard(user: User) -> bool:
    """Admin panelidagi har bir handler shu tekshiruvdan o'tadi."""
    return bool(user and user.is_admin)


@router.message(Command("admin"))
async def cmd_admin(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    if not _guard(user):
        return
    await state.clear()
    await _render_home(message, session, _, edit=False)


@router.callback_query(MenuCB.filter(F.action == "admin"))
@router.callback_query(AdminCB.filter(F.action == "home"))
async def admin_home(
    query: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await state.clear()
    await _render_home(query, session, _)
    await query.answer()


async def _render_home(
    event: CallbackQuery | Message, session: AsyncSession, _: Translator, *, edit: bool = True
) -> None:
    pending = await withdrawals.count_pending(session)

    builder = InlineKeyboardBuilder()
    builder.button(text=_("admin.stats_btn"), callback_data=AdminCB(action="stats"))
    builder.button(text=_("admin.users_btn"), callback_data=AdminCB(action="users"))
    builder.button(
        text=_("admin.withdrawals_btn", count=pending),
        callback_data=AdminCB(action="withdrawals"),
    )
    builder.button(text=_("admin.rates_btn"), callback_data=AdminCB(action="rates"))
    builder.button(text=_("admin.settings_btn"), callback_data=AdminCB(action="settings"))
    builder.button(text=_("admin.broadcast_btn"), callback_data=AdminCB(action="broadcast"))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="home"))
    builder.adjust(2, 1, 2, 1, 1)

    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, _("admin.title"), builder.as_markup())
    else:
        message = event.message if isinstance(event, CallbackQuery) else event
        await message.answer(_("admin.title"), reply_markup=builder.as_markup())


# --------------------------------------------------------------------------
# Statistika
# --------------------------------------------------------------------------


@router.callback_query(AdminCB.filter(F.action == "stats"))
async def show_stats(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return

    async def scalar(stmt) -> int:
        return int((await session.execute(stmt)).scalar_one() or 0)

    today = utcnow() - timedelta(days=1)
    week = utcnow() - timedelta(days=7)

    total_users = await scalar(select(func.count(User.id)))
    users_today = await scalar(select(func.count(User.id)).where(User.created_at >= today))
    active = await scalar(select(func.count(User.id)).where(User.last_seen_at >= week))
    chats_count = await scalar(select(func.count(ChatSettings.chat_id)).where(ChatSettings.enabled.is_(True)))

    total_balance = await scalar(select(func.coalesce(func.sum(Wallet.balance_mxtr), 0)))
    total_locked = await scalar(select(func.coalesce(func.sum(Wallet.locked_mxtr), 0)))

    topup_sum = await scalar(
        select(func.coalesce(func.sum(Payment.amount_mxtr), 0)).where(
            Payment.status == PaymentStatus.PAID
        )
    )
    topup_count = await scalar(
        select(func.count(Payment.id)).where(Payment.status == PaymentStatus.PAID)
    )
    paid_messages = await scalar(
        select(func.count(RelayMessage.id)).where(RelayMessage.price_mxtr > 0)
    )
    commission = await scalar(
        select(func.coalesce(func.sum(RelayMessage.commission_mxtr), 0))
    )
    withdrawn = await scalar(
        select(func.coalesce(func.sum(Withdrawal.amount_mxtr), 0)).where(
            Withdrawal.status == WithdrawStatus.PAID
        )
    )
    pending_sum = await scalar(
        select(func.coalesce(func.sum(Withdrawal.amount_mxtr), 0)).where(
            Withdrawal.status.in_(WithdrawStatus.OPEN_STATES)
        )
    )

    text = _(
        "admin.stats",
        users=total_users,
        users_today=users_today,
        active=active,
        chats=chats_count,
        balance=fmt(total_balance),
        locked=fmt(total_locked),
        topup=fmt(topup_sum),
        topup_count=topup_count,
        messages=paid_messages,
        commission=fmt(commission),
        withdrawn=fmt(withdrawn),
        pending=fmt(pending_sum),
    )
    await safe_edit(query, text, back_to_admin(_))
    await query.answer()


def back_to_admin(_: Translator):
    builder = InlineKeyboardBuilder()
    builder.button(text=_("common.back"), callback_data=AdminCB(action="home"))
    return builder.as_markup()


# --------------------------------------------------------------------------
# Foydalanuvchilar
# --------------------------------------------------------------------------


@router.callback_query(AdminCB.filter(F.action == "users"))
async def users_search(
    query: CallbackQuery, state: FSMContext, user: User, _: Translator
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await state.set_state(AdminSG.user_search)
    await safe_edit(query, _("admin.user_search"), back_to_admin(_))
    await query.answer()


@router.message(AdminSG.user_search)
async def find_user(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    if not _guard(user):
        return
    target = await users.resolve(session, message.text or "")
    if target is None:
        await message.answer(_("cmd.user_not_found"))
        return
    await state.clear()
    await _render_user_card(message, session, target, _, fmt, edit=False)


@router.callback_query(AdminCB.filter(F.action == "user_open"))
async def open_user(
    query: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    target = await session.get(User, callback_data.item_id)
    if target is None:
        await query.answer(_("error.not_found"), show_alert=True)
        return
    await _render_user_card(query, session, target, _, fmt)
    await query.answer()


async def _render_user_card(
    event: CallbackQuery | Message,
    session: AsyncSession,
    target: User,
    _: Translator,
    fmt,
    *,
    edit: bool = True,
) -> None:
    row = await wallet.get_wallet(session, target.id)
    tz_hours = target.tz_offset_minutes // 60

    text = _(
        "admin.user_card",
        name=target.full_name,
        id=target.id,
        username=f"@{target.username}" if target.username else "",
        language=target.language,
        tz=f"{'+' if tz_hours >= 0 else ''}{tz_hours}",
        premium="✅" if target.is_premium else "❌",
        balance=fmt(row.balance_mxtr),
        locked=fmt(row.locked_mxtr),
        topup=fmt(row.total_topup_mxtr),
        earned=fmt(row.total_earned_mxtr),
        withdrawn=fmt(row.total_withdrawn_mxtr),
        joined=target.created_at.strftime("%d.%m.%Y"),
        banned="✅" if target.is_banned else "❌",
    )

    builder = InlineKeyboardBuilder()
    builder.button(text=_("admin.credit_btn"), callback_data=AdminCB(action="credit", item_id=target.id))
    builder.button(text=_("admin.debit_btn"), callback_data=AdminCB(action="debit", item_id=target.id))
    if target.is_banned:
        builder.button(text=_("admin.unban_btn"), callback_data=AdminCB(action="unban", item_id=target.id))
    else:
        builder.button(text=_("admin.ban_btn"), callback_data=AdminCB(action="ban", item_id=target.id))
    builder.button(text=_("common.back"), callback_data=AdminCB(action="home"))
    builder.adjust(2, 1, 1)

    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        message = event.message if isinstance(event, CallbackQuery) else event
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(AdminCB.filter(F.action.in_({"credit", "debit"})))
async def ask_amount(
    query: CallbackQuery,
    callback_data: AdminCB,
    state: FSMContext,
    user: User,
    _: Translator,
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await state.update_data(target_id=callback_data.item_id)
    if callback_data.action == "credit":
        await state.set_state(AdminSG.credit)
        text = _("admin.credit_prompt")
    else:
        await state.set_state(AdminSG.debit)
        text = _("admin.debit_prompt")
    await safe_edit(query, text, back_to_admin(_))
    await query.answer()


@router.message(AdminSG.credit)
@router.message(AdminSG.debit)
async def apply_amount(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    if not _guard(user):
        return
    value = parse_amount(message.text or "")
    if value is None or value <= 0:
        await message.answer(_("error.invalid_number"))
        return

    data = await state.get_data()
    target = await session.get(User, int(data.get("target_id", 0)))
    if target is None:
        await state.clear()
        await message.answer(_("error.not_found"))
        return

    amount_mxtr = stars_to_mxtr(value)
    is_credit = (await state.get_state()) == AdminSG.credit.state
    await state.clear()

    try:
        if is_credit:
            await wallet.credit(
                session, target.id, amount_mxtr, TxKind.ADMIN_CREDIT,
                counterparty_id=user.id, note=f"admin:{user.id}",
            )
        else:
            await wallet.debit(
                session, target.id, amount_mxtr, TxKind.ADMIN_DEBIT,
                allow_locked=True, counterparty_id=user.id, note=f"admin:{user.id}",
            )
    except wallet.InsufficientFunds:
        await message.answer(_("withdraw.not_enough", min="—", available="—"))
        return

    session.add(
        AuditLog(
            actor_id=user.id,
            action="credit" if is_credit else "debit",
            target=str(target.id),
            payload={"amount_mxtr": amount_mxtr},
        )
    )
    balance, _available = await wallet.balance(session, target.id)
    await message.answer(_("admin.amount_done", balance=fmt(balance)))
    await _render_user_card(message, session, target, _, fmt, edit=False)


@router.callback_query(AdminCB.filter(F.action == "ban"))
async def ask_ban(
    query: CallbackQuery, callback_data: AdminCB, state: FSMContext, user: User, _: Translator
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await state.set_state(AdminSG.ban_reason)
    await state.update_data(target_id=callback_data.item_id)
    await safe_edit(query, _("admin.ban_prompt"), back_to_admin(_))
    await query.answer()


@router.message(AdminSG.ban_reason)
async def apply_ban(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    if not _guard(user):
        return
    data = await state.get_data()
    target = await session.get(User, int(data.get("target_id", 0)))
    await state.clear()
    if target is None:
        await message.answer(_("error.not_found"))
        return

    target.is_banned = True
    target.ban_reason = (message.text or "")[:256]
    session.add(
        AuditLog(actor_id=user.id, action="ban", target=str(target.id), payload={"reason": target.ban_reason})
    )
    await session.flush()
    await message.answer(_("admin.banned_done"))
    await _render_user_card(message, session, target, _, fmt, edit=False)


@router.callback_query(AdminCB.filter(F.action == "unban"))
async def unban(
    query: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    target = await session.get(User, callback_data.item_id)
    if target is None:
        await query.answer(_("error.not_found"), show_alert=True)
        return
    target.is_banned = False
    target.ban_reason = None
    await session.flush()
    await query.answer(_("admin.unbanned_done"))
    await _render_user_card(query, session, target, _, fmt)


# --------------------------------------------------------------------------
# Pul yechish so'rovlari
# --------------------------------------------------------------------------


@router.callback_query(AdminCB.filter(F.action == "withdrawals"))
async def list_withdrawals(
    query: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return

    page = max(0, callback_data.page)
    rows = await withdrawals.list_pending(session, limit=9, offset=page * 8)
    has_next = len(rows) > 8
    rows = rows[:8]

    if not rows and page == 0:
        await safe_edit(query, _("admin.withdrawals_empty"), back_to_admin(_))
        await query.answer()
        return

    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.button(
            text=f"#{row.id} • {fmt(row.amount_mxtr)} • {_(f'withdraw.status.{row.status}')}"[:60],
            callback_data=AdminCB(action="wd_open", item_id=row.id),
        )
    builder.adjust(1)

    builder.row()
    if page > 0:
        builder.button(text=_("common.prev"), callback_data=AdminCB(action="withdrawals", page=page - 1))
    if has_next:
        builder.button(text=_("common.next"), callback_data=AdminCB(action="withdrawals", page=page + 1))
    builder.row()
    builder.button(text=_("common.back"), callback_data=AdminCB(action="home"))

    await safe_edit(query, _("admin.withdrawals_title"), builder.as_markup())
    await query.answer()


@router.callback_query(AdminCB.filter(F.action == "wd_open"))
async def open_withdrawal(
    query: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await _render_withdrawal(query, session, callback_data.item_id, _, fmt)
    await query.answer()


async def _render_withdrawal(
    event: CallbackQuery | Message,
    session: AsyncSession,
    request_id: int,
    _: Translator,
    fmt,
    *,
    edit: bool = True,
) -> None:
    request = await session.get(Withdrawal, request_id)
    message = event.message if isinstance(event, CallbackQuery) else event
    if request is None:
        await message.answer(_("error.not_found"))
        return

    target = await session.get(User, request.user_id)
    flags = (request.risk_flags or {}).get("flags", [])

    text = _(
        "admin.withdrawal_card",
        id=request.id,
        user=target.mention if target else "—",
        user_id=request.user_id,
        amount=fmt(request.amount_mxtr),
        fee=fmt(request.fee_mxtr),
        net=fmt(request.net_mxtr),
        method=_(f"withdraw.method.{request.method}"),
        destination=request.destination,
        destination_name=request.destination_name or "—",
        date=request.created_at.strftime("%d.%m.%Y %H:%M"),
        risk=request.risk_score,
        flags=("⚑ " + ", ".join(flags)) if flags else "",
    )
    text += f"\n\n💳 {request.payout_amount} {request.payout_currency}"
    text += f"\n📌 {_(f'withdraw.status.{request.status}')}"

    builder = InlineKeyboardBuilder()
    if request.status == WithdrawStatus.PENDING:
        builder.button(text=_("admin.approve_btn"), callback_data=AdminCB(action="wd_approve", item_id=request.id))
        builder.button(text=_("admin.reject_btn"), callback_data=AdminCB(action="wd_reject", item_id=request.id))
    if request.status in WithdrawStatus.OPEN_STATES:
        builder.button(text=_("admin.mark_paid_btn"), callback_data=AdminCB(action="wd_paid", item_id=request.id))
        builder.button(text=_("admin.reject_btn"), callback_data=AdminCB(action="wd_reject", item_id=request.id))
    builder.button(text=_("common.back"), callback_data=AdminCB(action="withdrawals"))
    builder.adjust(2, 2, 1)

    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(AdminCB.filter(F.action == "wd_approve"))
async def approve_withdrawal(
    query: CallbackQuery,
    callback_data: AdminCB,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return

    request = await session.get(Withdrawal, callback_data.item_id)
    if request is None:
        await query.answer(_("error.not_found"), show_alert=True)
        return
    try:
        await withdrawals.approve(session, request, user.id)
    except withdrawals.WithdrawError:
        await query.answer(_("withdraw.cannot_cancel"), show_alert=True)
        return

    await query.answer(_("admin.withdrawal_updated"))
    target = await session.get(User, request.user_id)
    if target is not None:
        translator = Translator(target.language)
        await notify(bot, target.id, translator("withdraw.approved_notice", id=request.id))
    await _render_withdrawal(query, session, request.id, _, fmt)


@router.callback_query(AdminCB.filter(F.action == "wd_reject"))
async def ask_reject_reason(
    query: CallbackQuery, callback_data: AdminCB, state: FSMContext, user: User, _: Translator
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await state.set_state(AdminSG.reject_reason)
    await state.update_data(request_id=callback_data.item_id)
    await safe_edit(query, _("admin.reject_prompt"), back_to_admin(_))
    await query.answer()


@router.message(AdminSG.reject_reason)
async def apply_reject(
    message: Message,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    if not _guard(user):
        return
    data = await state.get_data()
    request = await session.get(Withdrawal, int(data.get("request_id", 0)))
    await state.clear()
    if request is None:
        await message.answer(_("error.not_found"))
        return

    reason = (message.text or "—")[:256]
    try:
        await withdrawals.reject(session, request, user.id, reason)
    except withdrawals.WithdrawError:
        await message.answer(_("withdraw.cannot_cancel"))
        return

    target = await session.get(User, request.user_id)
    if target is not None:
        translator = Translator(target.language)
        target_fmt = await make_fmt(session, target.display_currency)
        await notify(
            bot,
            target.id,
            translator(
                "withdraw.rejected_notice",
                id=request.id,
                reason=reason,
                amount=target_fmt(request.amount_mxtr),
            ),
        )

    await message.answer(_("admin.withdrawal_updated"))
    await _render_withdrawal(message, session, request.id, _, fmt, edit=False)


@router.callback_query(AdminCB.filter(F.action == "wd_paid"))
async def ask_paid_ref(
    query: CallbackQuery, callback_data: AdminCB, state: FSMContext, user: User, _: Translator
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await state.set_state(AdminSG.paid_ref)
    await state.update_data(request_id=callback_data.item_id)
    await safe_edit(query, _("admin.paid_prompt"), back_to_admin(_))
    await query.answer()


@router.message(AdminSG.paid_ref)
async def apply_paid(
    message: Message,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    if not _guard(user):
        return
    data = await state.get_data()
    request = await session.get(Withdrawal, int(data.get("request_id", 0)))
    await state.clear()
    if request is None:
        await message.answer(_("error.not_found"))
        return

    raw = (message.text or "").strip()
    external_ref = None if raw == "-" else raw[:128]

    try:
        await withdrawals.mark_paid(session, request, user.id, external_ref)
    except withdrawals.WithdrawError:
        await message.answer(_("withdraw.cannot_cancel"))
        return
    except wallet.InsufficientFunds:
        await message.answer(_("error.generic"))
        return

    session.add(
        AuditLog(
            actor_id=user.id, action="withdraw_paid", target=str(request.id),
            payload={"ref": external_ref, "amount_mxtr": request.amount_mxtr},
        )
    )

    target = await session.get(User, request.user_id)
    if target is not None:
        translator = Translator(target.language)
        target_fmt = await make_fmt(session, target.display_currency)
        await notify(
            bot,
            target.id,
            translator(
                "withdraw.paid_notice",
                id=request.id,
                net=target_fmt(request.net_mxtr),
                destination=request.destination,
                ref=f"🧾 {external_ref}" if external_ref else "",
            ),
        )

    await message.answer(_("admin.withdrawal_updated"))
    await _render_withdrawal(message, session, request.id, _, fmt, edit=False)


# --------------------------------------------------------------------------
# Kurslar va sozlamalar
# --------------------------------------------------------------------------


@router.callback_query(AdminCB.filter(F.action == "rates"))
async def show_rates(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return

    rate_uzs, rate_usd = await app_settings.rates(session)
    commission = await app_settings.commission_bps(session)

    builder = InlineKeyboardBuilder()
    builder.button(text=_("admin.rate_uzs_btn"), callback_data=AdminCB(action="set_rate", value="rate_uzs_per_star"))
    builder.button(text=_("admin.rate_usd_btn"), callback_data=AdminCB(action="set_rate", value="rate_usd_per_star"))
    builder.button(text=_("admin.commission_btn"), callback_data=AdminCB(action="set_rate", value="commission_bps"))
    builder.button(text=_("common.back"), callback_data=AdminCB(action="home"))
    builder.adjust(2, 1, 1)

    await safe_edit(
        query,
        _("admin.rates_title", uzs=rate_uzs, usd=rate_usd, commission=round(commission / 100, 2)),
        builder.as_markup(),
    )
    await query.answer()


@router.callback_query(AdminCB.filter(F.action == "set_rate"))
async def ask_rate(
    query: CallbackQuery, callback_data: AdminCB, state: FSMContext, user: User, _: Translator
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await state.set_state(AdminSG.rate_value)
    await state.update_data(key=callback_data.value)
    text = _("admin.commission_prompt") if callback_data.value == "commission_bps" else _("admin.rate_prompt")
    await safe_edit(query, text, back_to_admin(_))
    await query.answer()


@router.message(AdminSG.rate_value)
async def save_rate(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    if not _guard(user):
        return
    value = parse_amount(message.text or "")
    if value is None:
        await message.answer(_("error.invalid_number"))
        return

    data = await state.get_data()
    key = data.get("key", "")
    await state.clear()

    if key == "commission_bps":
        await app_settings.set_value(session, key, int(value * 100))
    else:
        await app_settings.set_value(session, key, float(value))

    session.add(AuditLog(actor_id=user.id, action="set_setting", target=key, payload={"value": str(value)}))
    await message.answer(_("admin.rate_saved"))


@router.callback_query(AdminCB.filter(F.action == "settings"))
async def system_settings(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await _render_settings(query, session, _, fmt)
    await query.answer()


async def _render_settings(
    query: CallbackQuery, session: AsyncSession, _: Translator, fmt
) -> None:
    values = await app_settings.load(session, force=True)

    text = _(
        "admin.settings_title",
        min_topup=f"{values['min_topup_stars']} ⭐",
        min_withdraw=f"{values['min_withdraw_stars']} ⭐",
        withdraw_fee=round(int(values["withdraw_fee_bps"]) / 100, 2),
        hold=values["withdraw_hold_hours"],
        maintenance="✅" if values["maintenance"] else "❌",
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("admin.toggle_maintenance", state="✅" if values["maintenance"] else "❌"),
        callback_data=AdminCB(action="toggle", value="maintenance"),
    )
    builder.button(
        text=_("admin.toggle_withdraw", state="✅" if values["withdraw_enabled"] else "❌"),
        callback_data=AdminCB(action="toggle", value="withdraw_enabled"),
    )
    builder.button(text=_("common.back"), callback_data=AdminCB(action="home"))
    builder.adjust(1)

    await safe_edit(query, text, builder.as_markup())


@router.callback_query(AdminCB.filter(F.action == "toggle"))
async def toggle_setting(
    query: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    current = await app_settings.get(session, callback_data.value, False)
    await app_settings.set_value(session, callback_data.value, not bool(current))
    await query.answer()
    await _render_settings(query, session, _, fmt)


# --------------------------------------------------------------------------
# Xabar tarqatish
# --------------------------------------------------------------------------


@router.callback_query(AdminCB.filter(F.action == "broadcast"))
async def ask_broadcast(
    query: CallbackQuery, state: FSMContext, user: User, _: Translator
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return
    await state.set_state(AdminSG.broadcast_text)
    await safe_edit(query, _("admin.broadcast_prompt"), back_to_admin(_))
    await query.answer()


@router.message(AdminSG.broadcast_text)
async def confirm_broadcast(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    if not _guard(user):
        return
    text = message.html_text or message.text or ""
    if not text.strip():
        await message.answer(_("error.generic"))
        return

    total = int(
        (
            await session.execute(
                select(func.count(User.id)).where(User.is_banned.is_(False))
            )
        ).scalar_one()
    )

    await state.set_state(AdminSG.broadcast_confirm)
    await state.update_data(text=text)

    builder = InlineKeyboardBuilder()
    builder.button(text=_("common.yes"), callback_data=AdminCB(action="bc_go"))
    builder.button(text=_("common.cancel"), callback_data=AdminCB(action="home"))
    builder.adjust(2)

    await message.answer(
        _("admin.broadcast_confirm", count=total, preview=text[:500]),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(AdminCB.filter(F.action == "bc_go"), AdminSG.broadcast_confirm)
async def run_broadcast(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    if not _guard(user):
        await query.answer(_("error.no_access"), show_alert=True)
        return

    data = await state.get_data()
    text = data.get("text", "")
    await state.clear()

    await safe_edit(query, _("admin.broadcast_started"))
    await query.answer()

    rows = (
        await session.execute(select(User.id).where(User.is_banned.is_(False)))
    ).scalars().all()

    sent = failed = 0
    for index, user_id in enumerate(rows, start=1):
        if await notify(bot, user_id, text):
            sent += 1
        else:
            failed += 1
        # Telegram cheklovi: sekundiga ~30 xabar
        if index % 25 == 0:
            await asyncio.sleep(1)

    session.add(
        AuditLog(actor_id=user.id, action="broadcast", payload={"sent": sent, "failed": failed})
    )
    await notify(bot, user.id, _("admin.broadcast_done", sent=sent, failed=failed))
