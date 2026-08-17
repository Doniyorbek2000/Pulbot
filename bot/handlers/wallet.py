"""Hamyon: balans, to'ldirish, tranzaksiyalar tarixi va Stars to'lovlari."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.handlers.common import parse_amount, render_main_menu, safe_edit
from bot.i18n import Translator
from bot.keyboards.callbacks import MenuCB, WalletCB
from bot.keyboards.menus import back_to, cancel_keyboard
from bot.services import app_settings, payments, wallet
from bot.states import TopupSG
from bot.utils.money import format_amount, stars_to_mxtr

logger = logging.getLogger(__name__)

router = Router(name="wallet")

HISTORY_PAGE_SIZE = 8


# --------------------------------------------------------------------------
# Balans
# --------------------------------------------------------------------------


@router.callback_query(MenuCB.filter(F.action == "wallet"))
async def open_wallet(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    await _render_wallet(query, session, user, _, fmt)
    await query.answer()


@router.message(Command("balans", "balance", "hamyon"))
async def cmd_wallet(
    message: Message, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    await _render_wallet(message, session, user, _, fmt, edit=False)


async def _render_wallet(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
    *,
    edit: bool = True,
) -> None:
    row = await wallet.get_wallet(session, user.id)
    text = _(
        "wallet.title",
        balance=fmt(row.balance_mxtr),
        available=fmt(row.available_mxtr),
        locked=fmt(row.locked_mxtr),
        earned=fmt(row.total_earned_mxtr),
        spent=fmt(row.total_spent_mxtr),
        topup=fmt(row.total_topup_mxtr),
        withdrawn=fmt(row.total_withdrawn_mxtr),
    )
    if row.locked_mxtr:
        text += "\n\n" + _("wallet.locked_hint")

    builder = InlineKeyboardBuilder()
    builder.button(text=_("wallet.topup"), callback_data=WalletCB(action="topup"))
    builder.button(text=_("wallet.withdraw"), callback_data=MenuCB(action="withdraw"))
    builder.button(text=_("wallet.history"), callback_data=WalletCB(action="history"))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="home"))
    builder.adjust(2, 1, 1)

    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        message = event.message if isinstance(event, CallbackQuery) else event
        await message.answer(text, reply_markup=builder.as_markup())


# --------------------------------------------------------------------------
# To'ldirish
# --------------------------------------------------------------------------


@router.callback_query(WalletCB.filter(F.action == "topup"))
async def topup_menu(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    await show_topup(query, session, user, _, fmt)
    await query.answer()


async def show_topup(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
    *,
    edit: bool = True,
) -> None:
    presets = await app_settings.get(session, "topup_presets_stars", [50, 100, 250, 500])
    rate_uzs, rate_usd = await app_settings.rates(session)

    builder = InlineKeyboardBuilder()
    for stars in presets:
        label = f"{stars} ⭐ · {format_amount(stars_to_mxtr(int(stars)), user.display_currency, rate_uzs=rate_uzs, rate_usd=rate_usd)}"
        builder.button(text=label, callback_data=WalletCB(action="invoice", value=int(stars)))
    builder.button(text=_("topup.custom"), callback_data=WalletCB(action="custom"))
    builder.button(text=_("topup.other_methods"), callback_data=WalletCB(action="other"))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="wallet"))
    builder.adjust(2, 2, 2, 1, 1, 1)

    text = _("topup.title")
    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        message = event.message if isinstance(event, CallbackQuery) else event
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(WalletCB.filter(F.action == "custom"))
async def topup_custom(
    query: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    min_stars = int(await app_settings.get(session, "min_topup_stars", 1))
    rate_uzs, rate_usd = await app_settings.rates(session)
    await state.set_state(TopupSG.amount)
    await safe_edit(
        query,
        _(
            "topup.enter_amount",
            min=min_stars,
            rate=format_amount(
                stars_to_mxtr(1), user.display_currency, rate_uzs=rate_uzs, rate_usd=rate_usd
            ),
        ),
        cancel_keyboard(_, "wallet"),
    )
    await query.answer()


@router.message(TopupSG.amount)
async def topup_amount(
    message: Message,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    value = parse_amount(message.text or "")
    if value is None:
        await message.answer(_("error.invalid_number"))
        return

    stars = int(value)
    min_stars = int(await app_settings.get(session, "min_topup_stars", 1))
    if stars < min_stars:
        await message.answer(_("error.too_small", min=f"{min_stars} ⭐"))
        return
    if stars > 1_000_000:
        await message.answer(_("error.too_big", max="1 000 000 ⭐"))
        return

    await state.clear()
    await payments.create_star_invoice(bot, session, user=user, stars=stars)


@router.callback_query(WalletCB.filter(F.action == "invoice"))
async def topup_invoice(
    query: CallbackQuery,
    callback_data: WalletCB,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    stars = max(1, callback_data.value)
    await query.answer()
    await payments.create_star_invoice(bot, session, user=user, stars=stars)


@router.callback_query(WalletCB.filter(F.action == "other"))
async def topup_other(
    query: CallbackQuery, session: AsyncSession, _: Translator
) -> None:
    support = await app_settings.get(session, "support_username", "") or "—"
    if support != "—" and not support.startswith("@"):
        support = f"@{support}"
    await safe_edit(query, _("topup.other_methods_text", support=support), back_to(_, "wallet"))
    await query.answer()


# --------------------------------------------------------------------------
# Telegram Stars to'lovi
# --------------------------------------------------------------------------


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, session: AsyncSession) -> None:
    """Telegram to'lovni yakunlashdan oldin so'raydi — 10 soniyada javob berish shart."""
    payment = await payments.find_by_payload(session, query.invoice_payload)
    if payment is None:
        await query.answer(ok=False, error_message="Invoice not found / To'lov topilmadi")
        return
    if payment.user_id != query.from_user.id:
        await query.answer(ok=False, error_message="Payload mismatch")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(
    message: Message, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    """To'lov muvaffaqiyatli — balansga qo'shamiz."""
    successful = message.successful_payment
    payment = await payments.find_by_payload(session, successful.invoice_payload)

    if payment is None:
        # Yozuv topilmadi (masalan, DB tiklangan) — baribir hisoblaymiz
        logger.warning("To'lov yozuvi topilmadi: %s", successful.invoice_payload)
        from bot.db.enums import PaymentProvider, PaymentStatus
        from bot.db.models import Payment

        payment = Payment(
            user_id=user.id,
            provider=PaymentProvider.STARS,
            status=PaymentStatus.PENDING,
            amount_mxtr=stars_to_mxtr(successful.total_amount),
            stars=successful.total_amount,
            payload=successful.invoice_payload,
        )
        session.add(payment)
        await session.flush()

    balance = await payments.credit_payment(session, payment, successful)

    await message.answer(
        _(
            "topup.success",
            amount=fmt(payment.amount_mxtr),
            balance=fmt(balance),
            charge_id=successful.telegram_payment_charge_id,
        )
    )
    await render_main_menu(message, session, user, _, fmt, edit=False)


# --------------------------------------------------------------------------
# Tarix
# --------------------------------------------------------------------------


@router.callback_query(WalletCB.filter(F.action == "history"))
async def history(
    query: CallbackQuery,
    callback_data: WalletCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    page = max(0, callback_data.page)
    rows = await wallet.history(
        session, user.id, limit=HISTORY_PAGE_SIZE + 1, offset=page * HISTORY_PAGE_SIZE
    )
    has_next = len(rows) > HISTORY_PAGE_SIZE
    rows = rows[:HISTORY_PAGE_SIZE]

    if not rows and page == 0:
        await safe_edit(query, _("history.empty"), back_to(_, "wallet"))
        await query.answer()
        return

    lines = [_("history.title"), ""]
    for tx in rows:
        sign = "➕" if tx.amount_mxtr > 0 else ("➖" if tx.amount_mxtr < 0 else "•")
        amount = fmt(abs(tx.amount_mxtr)) if tx.amount_mxtr else "—"
        label = _(f"history.kind.{tx.kind}")
        stamp = tx.created_at.strftime("%d.%m %H:%M")
        lines.append(f"{sign} <b>{amount}</b> · {label}\n<i>{stamp}</i>")

    builder = InlineKeyboardBuilder()
    if page > 0:
        builder.button(text=_("common.prev"), callback_data=WalletCB(action="history", page=page - 1))
    if has_next:
        builder.button(text=_("common.next"), callback_data=WalletCB(action="history", page=page + 1))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="wallet"))
    builder.adjust(2, 1)

    await safe_edit(query, "\n\n".join(lines), builder.as_markup())
    await query.answer()
