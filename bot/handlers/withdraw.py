"""Pul yechish: so'rov yaratish, ro'yxat, bekor qilish."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.enums import WithdrawMethod, WithdrawStatus
from bot.db.models import User, Withdrawal
from bot.handlers.common import notify, parse_amount, safe_edit
from bot.i18n import Translator
from bot.keyboards.callbacks import MenuCB, WithdrawCB
from bot.keyboards.menus import back_to, cancel_keyboard
from bot.services import app_settings, withdrawals
from bot.states import WithdrawSG
from bot.utils.money import from_currency, stars_to_mxtr

logger = logging.getLogger(__name__)

router = Router(name="withdraw")


# --------------------------------------------------------------------------
# Bosh ekran
# --------------------------------------------------------------------------


@router.callback_query(MenuCB.filter(F.action == "withdraw"))
@router.callback_query(WithdrawCB.filter(F.action == "home"))
async def open_withdraw(
    query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    await state.clear()
    await _render_home(query, session, user, _, fmt)
    await query.answer()


@router.message(Command("yechish", "withdraw", "vyvod"))
async def cmd_withdraw(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    await state.clear()
    await _render_home(message, session, user, _, fmt, edit=False)


async def _render_home(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
    *,
    edit: bool = True,
) -> None:
    free_mxtr, held_mxtr = await withdrawals.available_to_withdraw(session, user.id)
    min_stars = int(await app_settings.get(session, "min_withdraw_stars", 1000))
    fee_bps = int(await app_settings.get(session, "withdraw_fee_bps", 200))
    hold_hours = int(await app_settings.get(session, "withdraw_hold_hours", 72))

    text = _(
        "withdraw.title",
        available=fmt(free_mxtr),
        min=fmt(stars_to_mxtr(min_stars)),
        fee=round(fee_bps / 100, 2),
        hold=hold_hours,
    )
    if held_mxtr:
        text += "\n\n" + _("withdraw.held_funds", amount=fmt(held_mxtr), hours=hold_hours)

    enabled = await app_settings.get(session, "withdraw_enabled", True)
    if not enabled:
        text += "\n\n" + _("withdraw.disabled")

    builder = InlineKeyboardBuilder()
    if enabled:
        builder.button(text=_("withdraw.new_btn"), callback_data=WithdrawCB(action="new"))
    builder.button(text=_("withdraw.my_requests"), callback_data=WithdrawCB(action="list"))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="wallet"))
    builder.adjust(1)

    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        message = event.message if isinstance(event, CallbackQuery) else event
        await message.answer(text, reply_markup=builder.as_markup())


# --------------------------------------------------------------------------
# Yangi so'rov
# --------------------------------------------------------------------------


@router.callback_query(WithdrawCB.filter(F.action == "new"))
async def new_request(
    query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    if not await app_settings.get(session, "withdraw_enabled", True):
        await query.answer(_("withdraw.disabled"), show_alert=True)
        return

    free_mxtr, _held = await withdrawals.available_to_withdraw(session, user.id)
    min_stars = int(await app_settings.get(session, "min_withdraw_stars", 1000))
    min_mxtr = stars_to_mxtr(min_stars)

    if free_mxtr < min_mxtr:
        await safe_edit(
            query,
            _("withdraw.not_enough", min=fmt(min_mxtr), available=fmt(free_mxtr)),
            back_to(_, "withdraw"),
        )
        await query.answer()
        return

    await state.set_state(WithdrawSG.amount)
    await safe_edit(
        query,
        _("withdraw.enter_amount", available=fmt(free_mxtr), min=fmt(min_mxtr)),
        cancel_keyboard(_, "withdraw"),
    )
    await query.answer()


@router.message(WithdrawSG.amount)
async def receive_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    value = parse_amount(message.text or "")
    if value is None or value <= 0:
        await message.answer(_("error.invalid_number"))
        return

    rate_uzs, rate_usd = await app_settings.rates(session)
    amount_mxtr = from_currency(value, user.display_currency, rate_uzs, rate_usd)

    free_mxtr, _held = await withdrawals.available_to_withdraw(session, user.id)
    min_stars = int(await app_settings.get(session, "min_withdraw_stars", 1000))
    min_mxtr = stars_to_mxtr(min_stars)

    if amount_mxtr < min_mxtr:
        await message.answer(_("error.too_small", min=fmt(min_mxtr)))
        return
    if amount_mxtr > free_mxtr:
        await message.answer(_("withdraw.not_enough", min=fmt(min_mxtr), available=fmt(free_mxtr)))
        return

    await state.update_data(amount_mxtr=amount_mxtr)
    await state.set_state(WithdrawSG.method)

    builder = InlineKeyboardBuilder()
    for method in WithdrawMethod.ALL:
        builder.button(
            text=_(f"withdraw.method.{method}"),
            callback_data=WithdrawCB(action="method", value=method),
        )
    builder.button(text=_("common.cancel"), callback_data=WithdrawCB(action="home"))
    builder.adjust(2, 2, 1, 1)

    await message.answer(_("withdraw.choose_method"), reply_markup=builder.as_markup())


@router.callback_query(WithdrawCB.filter(F.action == "method"), WithdrawSG.method)
async def choose_method(
    query: CallbackQuery, callback_data: WithdrawCB, state: FSMContext, _: Translator
) -> None:
    method = callback_data.value
    if method not in WithdrawMethod.ALL:
        await query.answer()
        return

    await state.update_data(method=method)
    await state.set_state(WithdrawSG.destination)
    await safe_edit(
        query,
        _(f"withdraw.enter_destination.{method}"),
        cancel_keyboard(_, "withdraw"),
    )
    await query.answer()


@router.message(WithdrawSG.destination)
async def receive_destination(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    data = await state.get_data()
    method = data.get("method", WithdrawMethod.CARD_UZS)
    destination = withdrawals.normalize_destination(method, message.text or "")

    if not withdrawals.validate_destination(method, destination):
        await message.answer(_(f"withdraw.enter_destination.{method}"))
        return

    await state.update_data(destination=destination)

    if method == WithdrawMethod.CARD_UZS:
        await state.set_state(WithdrawSG.holder_name)
        await message.answer(_("withdraw.enter_name"), reply_markup=cancel_keyboard(_, "withdraw"))
        return

    await _show_confirm(message, state, session, user, _, fmt)


@router.message(WithdrawSG.holder_name)
async def receive_holder(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer(_("withdraw.enter_name"))
        return
    await state.update_data(holder_name=name[:128])
    await _show_confirm(message, state, session, user, _, fmt)


async def _show_confirm(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    data = await state.get_data()
    amount_mxtr = int(data["amount_mxtr"])
    method = data["method"]

    fee_bps = int(await app_settings.get(session, "withdraw_fee_bps", 200))
    from bot.utils.money import apply_bps

    fee = apply_bps(amount_mxtr, fee_bps)

    await state.set_state(WithdrawSG.confirm)

    builder = InlineKeyboardBuilder()
    builder.button(text=_("common.yes"), callback_data=WithdrawCB(action="confirm"))
    builder.button(text=_("common.cancel"), callback_data=WithdrawCB(action="home"))
    builder.adjust(2)

    await message.answer(
        _(
            "withdraw.confirm",
            amount=fmt(amount_mxtr),
            fee=fmt(fee),
            net=fmt(amount_mxtr - fee),
            method=_(f"withdraw.method.{method}"),
            destination=data["destination"],
        ),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(WithdrawCB.filter(F.action == "confirm"), WithdrawSG.confirm)
async def confirm_request(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    data = await state.get_data()
    await state.clear()

    try:
        request = await withdrawals.create_request(
            session,
            user,
            amount_mxtr=int(data["amount_mxtr"]),
            method=data["method"],
            destination=data["destination"],
            destination_name=data.get("holder_name"),
        )
    except withdrawals.WithdrawError as exc:
        # `*_mxtr` parametrlari foydalanuvchi valyutasida ko'rsatiladi
        params = {
            key.removesuffix("_mxtr"): fmt(value) if key.endswith("_mxtr") else value
            for key, value in exc.params.items()
        }
        await safe_edit(query, _(exc.key, **params), back_to(_, "withdraw"))
        await query.answer()
        return

    await safe_edit(
        query,
        _(
            "withdraw.created",
            id=request.id,
            amount=fmt(request.amount_mxtr),
            net=fmt(request.net_mxtr),
        ),
        back_to(_, "withdraw"),
    )
    await query.answer()
    await _alert_admins(bot, session, request, user)


async def _alert_admins(
    bot: Bot, session: AsyncSession, request: Withdrawal, user: User
) -> None:
    from bot.utils.money import format_amount

    rate_uzs, rate_usd = await app_settings.rates(session)
    for admin_id in settings.admin_ids:
        admin = await session.get(User, admin_id)
        translator = Translator(admin.language if admin else settings.default_language)
        currency = admin.display_currency if admin else "UZS"
        text = translator(
            "admin.new_withdrawal_alert",
            id=request.id,
            user=user.mention,
            amount=format_amount(request.amount_mxtr, currency, rate_uzs=rate_uzs, rate_usd=rate_usd),
            net=format_amount(request.net_mxtr, currency, rate_uzs=rate_uzs, rate_usd=rate_usd),
            method=translator(f"withdraw.method.{request.method}"),
        )
        from bot.keyboards.callbacks import AdminCB

        builder = InlineKeyboardBuilder()
        builder.button(
            text=translator("admin.open_request"),
            callback_data=AdminCB(action="wd_open", item_id=request.id),
        )
        await notify(bot, admin_id, text, builder.as_markup())


# --------------------------------------------------------------------------
# So'rovlar ro'yxati
# --------------------------------------------------------------------------


@router.callback_query(WithdrawCB.filter(F.action == "list"))
async def list_requests(
    query: CallbackQuery,
    callback_data: WithdrawCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    page = max(0, callback_data.page)
    rows = await withdrawals.list_for_user(session, user.id, limit=6, offset=page * 5)
    has_next = len(rows) > 5
    rows = rows[:5]

    if not rows and page == 0:
        await safe_edit(query, _("withdraw.list_empty"), back_to(_, "withdraw"))
        await query.answer()
        return

    lines = [_("withdraw.list_title"), ""]
    builder = InlineKeyboardBuilder()
    for row in rows:
        lines.append(
            _(
                "withdraw.item",
                id=row.id,
                amount=fmt(row.amount_mxtr),
                status=_(f"withdraw.status.{row.status}"),
                date=row.created_at.strftime("%d.%m.%Y %H:%M"),
            )
        )
        if row.status in WithdrawStatus.OPEN_STATES:
            builder.button(
                text=f"❌ #{row.id}",
                callback_data=WithdrawCB(action="cancel", item_id=row.id),
            )
    builder.adjust(3)

    builder.row()
    if page > 0:
        builder.button(text=_("common.prev"), callback_data=WithdrawCB(action="list", page=page - 1))
    if has_next:
        builder.button(text=_("common.next"), callback_data=WithdrawCB(action="list", page=page + 1))
    builder.row()
    builder.button(text=_("common.back"), callback_data=WithdrawCB(action="home"))

    await safe_edit(query, "\n\n".join(lines), builder.as_markup())
    await query.answer()


@router.callback_query(WithdrawCB.filter(F.action == "cancel"))
async def cancel_request(
    query: CallbackQuery,
    callback_data: WithdrawCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    request = await session.get(Withdrawal, callback_data.item_id)
    if request is None or request.user_id != user.id:
        await query.answer(_("error.not_found"), show_alert=True)
        return

    try:
        await withdrawals.cancel_request(session, request)
    except withdrawals.WithdrawError:
        await query.answer(_("withdraw.cannot_cancel"), show_alert=True)
        return

    await query.answer(_("withdraw.canceled", id=request.id), show_alert=True)
    await _render_home(query, session, user, _, fmt)
