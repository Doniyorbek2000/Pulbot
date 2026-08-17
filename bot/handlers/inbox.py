"""Shaxsiy xabarlar sozlamalari: rejim, narx, limitlar, qo'shimchalar."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import AccessRuleKind, InboxMode, PricingUnit
from bot.db.models import AccessRule, InboxSchedule, User
from bot.handlers.common import (
    make_fmt,
    parse_amount,
    parse_price_input,
    price_bounds,
    price_example,
    safe_edit,
    unit_label,
)
from bot.i18n import Translator
from bot.keyboards.callbacks import CurrencyCB, InboxCB, MenuCB, RuleCB, SchedCB
from bot.keyboards.menus import cancel_keyboard
from bot.services import app_settings, users
from bot.states import InboxSG
from bot.utils.money import CURRENCIES, split_commission

logger = logging.getLogger(__name__)

router = Router(name="inbox")


# --------------------------------------------------------------------------
# Bosh ekran
# --------------------------------------------------------------------------


@router.callback_query(MenuCB.filter(F.action == "inbox"))
@router.callback_query(InboxCB.filter(F.action == "home"))
async def open_inbox(
    query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    await state.clear()
    await render_inbox(query, session, user, _)
    await query.answer()


async def render_inbox(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    *,
    edit: bool = True,
) -> None:
    inbox = await users.get_inbox(session, user.id)
    fmt = await make_fmt(session, inbox.price_currency)

    schedules = int(
        (
            await session.execute(
                select(func.count(InboxSchedule.id)).where(InboxSchedule.user_id == user.id)
            )
        ).scalar_one()
    )
    rules = int(
        (
            await session.execute(
                select(func.count(AccessRule.id)).where(
                    AccessRule.owner_id == user.id, AccessRule.chat_id == 0
                )
            )
        ).scalar_one()
    )

    text = _(
        "inbox.title",
        mode=_(f"mode.{inbox.mode}"),
        price=fmt(inbox.price_mxtr) if inbox.price_mxtr else _("common.free"),
        unit=unit_label(_, inbox),
        first_free="✅" if inbox.free_first_message else "❌",
        premium_free="✅" if inbox.free_for_premium else "❌",
        schedules=schedules,
        rules=rules,
        hold=f"{inbox.hold_hours}h" if inbox.hold_hours else "—",
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="💵 Narxni belgilash", callback_data=InboxCB(action="price"))
    builder.button(text="⏱ Hisoblash usuli (Tarif)", callback_data=InboxCB(action="unit"))
    builder.button(text="🟢 Pul to'lamaydiganlar (Oq ro'yxat)", callback_data=RuleCB(action="list", scope="dm"))
    builder.button(text="➕ Akkaunt qo'shish", callback_data=RuleCB(action="add", scope="dm", kind=AccessRuleKind.FREE))
    builder.button(text="👥 Notanishlar / Kontaktdagilar", callback_data=InboxCB(action="mode"))
    builder.button(text="🧩 Bepul imtiyozlar (Premium/1-xabar)", callback_data=InboxCB(action="extra"))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="home"))
    builder.adjust(2, 2, 1, 1, 1)

    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        message = event.message if isinstance(event, CallbackQuery) else event
        await message.answer(text, reply_markup=builder.as_markup())


# --------------------------------------------------------------------------
# Rejim
# --------------------------------------------------------------------------


@router.callback_query(InboxCB.filter(F.action == "mode"))
async def choose_mode(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator
) -> None:
    inbox = await users.get_inbox(session, user.id)
    lines = [_("inbox.choose_mode", current=_(f"mode.{inbox.mode}")), ""]
    builder = InlineKeyboardBuilder()
    for mode in InboxMode.ALL:
        mark = "🔘 " if mode == inbox.mode else ""
        builder.button(text=f"{mark}{_(f'mode.{mode}')}", callback_data=InboxCB(action="set_mode", value=mode))
        lines.append(f"<b>{_(f'mode.{mode}')}</b> — {_(f'mode.desc.{mode}')}")
    builder.button(text=_("common.back"), callback_data=InboxCB(action="home"))
    builder.adjust(1)
    await safe_edit(query, "\n".join(lines), builder.as_markup())
    await query.answer()


@router.callback_query(InboxCB.filter(F.action == "set_mode"))
async def set_mode(
    query: CallbackQuery,
    callback_data: InboxCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    if callback_data.value not in InboxMode.ALL:
        await query.answer(_("error.generic"), show_alert=True)
        return

    inbox = await users.get_inbox(session, user.id)
    inbox.mode = callback_data.value
    await session.flush()

    await query.answer(_("inbox.mode_saved", mode=_(f"mode.{inbox.mode}")))

    # Pullik rejim tanlandi-yu, narx belgilanmagan bo'lsa — darhol so'raymiz
    if inbox.mode in (InboxMode.PAID, InboxMode.PREMIUM_OR_PAID) and inbox.price_mxtr <= 0:
        await _ask_price(query, state, session, user, _)
        return
    await render_inbox(query, session, user, _)


# --------------------------------------------------------------------------
# Narx
# --------------------------------------------------------------------------


@router.callback_query(InboxCB.filter(F.action == "price"))
async def price_screen(
    query: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    await _ask_price(query, state, session, user, _)
    await query.answer()


async def _ask_price(
    query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    inbox = await users.get_inbox(session, user.id)
    fmt = await make_fmt(session, inbox.price_currency)
    min_mxtr, max_mxtr = await price_bounds(session)

    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("inbox.price_currency_btn"),
        callback_data=InboxCB(action="price_currency"),
    )
    builder.button(text=_("common.back"), callback_data=InboxCB(action="home"))
    builder.adjust(1)

    text = _(
        "inbox.price_prompt",
        currency=_(f"currency.{inbox.price_currency}"),
        example=price_example(inbox.price_currency),
        min=fmt(min_mxtr),
        max=fmt(max_mxtr),
    )
    await safe_edit(query, text, builder.as_markup())
    await state.set_state(InboxSG.price)


@router.callback_query(InboxCB.filter(F.action == "price_currency"))
async def price_currency(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator
) -> None:
    inbox = await users.get_inbox(session, user.id)
    builder = InlineKeyboardBuilder()
    for code in CURRENCIES:
        mark = "🔘 " if code == inbox.price_currency else ""
        builder.button(
            text=f"{mark}{_(f'currency.{code}')}",
            callback_data=CurrencyCB(code=code, scope="inbox"),
        )
    builder.button(text=_("common.back"), callback_data=InboxCB(action="price"))
    builder.adjust(1)
    await safe_edit(query, _("currency.choose"), builder.as_markup())
    await query.answer()


@router.callback_query(CurrencyCB.filter(F.scope == "inbox"))
async def set_price_currency(
    query: CallbackQuery,
    callback_data: CurrencyCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    inbox = await users.get_inbox(session, user.id)
    inbox.price_currency = callback_data.code
    await session.flush()
    await query.answer(_("currency.changed", currency=_(f"currency.{callback_data.code}")))
    await _ask_price(query, state, session, user, _)


@router.message(InboxSG.price)
async def save_price(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    inbox = await users.get_inbox(session, user.id)
    price_mxtr = await parse_price_input(session, message.text or "", inbox.price_currency)
    if price_mxtr is None:
        await message.answer(_("error.invalid_number"))
        return

    fmt = await make_fmt(session, inbox.price_currency)
    min_mxtr, max_mxtr = await price_bounds(session)

    if price_mxtr > 0 and price_mxtr < min_mxtr:
        await message.answer(_("error.too_small", min=fmt(min_mxtr)))
        return
    if price_mxtr > max_mxtr:
        await message.answer(_("error.too_big", max=fmt(max_mxtr)))
        return

    inbox.price_mxtr = price_mxtr
    # Narx qo'yilsa-yu rejim ochiq bo'lsa — pullik rejimga o'tkazamiz
    if price_mxtr > 0 and inbox.mode == InboxMode.OPEN:
        inbox.mode = InboxMode.PREMIUM_OR_PAID
    await session.flush()
    await state.clear()

    commission = await app_settings.commission_bps(session)
    net, _fee = split_commission(price_mxtr, commission)
    await message.answer(
        _(
            "inbox.price_saved",
            price=fmt(price_mxtr) if price_mxtr else _("common.free"),
            commission=round(commission / 100, 2),
            net=fmt(net),
        )
    )
    await render_inbox(message, session, user, _, edit=False)


@router.message(Command("narx", "price", "cena"), F.chat.type == "private")
async def cmd_price(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    """Tez narx belgilash: /narx 5000 (guruh varianti groups.py da)."""
    if not command.args:
        await state.set_state(InboxSG.price)
        inbox = await users.get_inbox(session, user.id)
        fmt = await make_fmt(session, inbox.price_currency)
        min_mxtr, max_mxtr = await price_bounds(session)
        await message.answer(
            _(
                "inbox.price_prompt",
                currency=_(f"currency.{inbox.price_currency}"),
                example=price_example(inbox.price_currency),
                min=fmt(min_mxtr),
                max=fmt(max_mxtr),
            ),
            reply_markup=cancel_keyboard(_, "inbox"),
        )
        return

    inbox = await users.get_inbox(session, user.id)
    price_mxtr = await parse_price_input(session, command.args, inbox.price_currency)
    if price_mxtr is None:
        await message.answer(_("cmd.price_usage"))
        return

    inbox.price_mxtr = price_mxtr
    if price_mxtr > 0 and inbox.mode == InboxMode.OPEN:
        inbox.mode = InboxMode.PREMIUM_OR_PAID
    await session.flush()

    fmt = await make_fmt(session, inbox.price_currency)
    await message.answer(_("cmd.price_set", price=fmt(price_mxtr)))


# --------------------------------------------------------------------------
# Hisoblash usuli
# --------------------------------------------------------------------------


@router.callback_query(InboxCB.filter(F.action == "unit"))
async def choose_unit(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator
) -> None:
    inbox = await users.get_inbox(session, user.id)
    builder = InlineKeyboardBuilder()
    for unit in PricingUnit.ALL:
        mark = "🔘 " if unit == inbox.pricing_unit else ""
        builder.button(
            text=f"{mark}{_(f'unit.{unit}', minutes=inbox.session_minutes)}",
            callback_data=InboxCB(action="set_unit", value=unit),
        )
    if inbox.pricing_unit == PricingUnit.PER_SESSION:
        builder.button(
            text=_("inbox.session_minutes_prompt").split("\n")[0],
            callback_data=InboxCB(action="session"),
        )
    builder.button(text=_("common.back"), callback_data=InboxCB(action="home"))
    builder.adjust(1)
    await safe_edit(query, _("inbox.choose_unit", minutes=inbox.session_minutes), builder.as_markup())
    await query.answer()


@router.callback_query(InboxCB.filter(F.action == "set_unit"))
async def set_unit(
    query: CallbackQuery,
    callback_data: InboxCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    if callback_data.value not in PricingUnit.ALL:
        await query.answer(_("error.generic"), show_alert=True)
        return
    inbox = await users.get_inbox(session, user.id)
    inbox.pricing_unit = callback_data.value
    await session.flush()
    await query.answer(_("inbox.unit_saved", unit=unit_label(_, inbox)))
    await choose_unit(query, session, user, _)


@router.callback_query(InboxCB.filter(F.action == "session"))
async def ask_session_minutes(
    query: CallbackQuery, state: FSMContext, _: Translator
) -> None:
    await state.set_state(InboxSG.session_minutes)
    await safe_edit(query, _("inbox.session_minutes_prompt"), cancel_keyboard(_, "inbox"))
    await query.answer()


@router.message(InboxSG.session_minutes)
async def save_session_minutes(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    value = parse_amount(message.text or "")
    if value is None or int(value) < 1:
        await message.answer(_("error.invalid_number"))
        return
    inbox = await users.get_inbox(session, user.id)
    inbox.session_minutes = min(int(value), 60 * 24 * 30)
    await session.flush()
    await state.clear()
    await message.answer(_("inbox.session_minutes_saved", minutes=inbox.session_minutes))
    await render_inbox(message, session, user, _, edit=False)


# --------------------------------------------------------------------------
# Limitlar
# --------------------------------------------------------------------------


@router.callback_query(InboxCB.filter(F.action == "limits"))
async def limits_screen(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator
) -> None:
    inbox = await users.get_inbox(session, user.id)
    text = _(
        "inbox.limits_title",
        daily=inbox.daily_message_limit or _("common.unlimited"),
        per_sender=inbox.per_sender_daily_limit or _("common.unlimited"),
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="📨", callback_data=InboxCB(action="daily_limit"))
    builder.button(text="👤", callback_data=InboxCB(action="sender_limit"))
    builder.button(text=_("common.back"), callback_data=InboxCB(action="home"))
    builder.adjust(2, 1)
    await safe_edit(query, text, builder.as_markup())
    await query.answer()


@router.callback_query(InboxCB.filter(F.action.in_({"daily_limit", "sender_limit"})))
async def ask_limit(
    query: CallbackQuery, callback_data: InboxCB, state: FSMContext, _: Translator
) -> None:
    if callback_data.action == "daily_limit":
        await state.set_state(InboxSG.daily_limit)
        text = _("inbox.daily_limit_prompt")
    else:
        await state.set_state(InboxSG.per_sender_limit)
        text = _("inbox.per_sender_prompt")
    await safe_edit(query, text, cancel_keyboard(_, "inbox"))
    await query.answer()


@router.message(InboxSG.daily_limit)
@router.message(InboxSG.per_sender_limit)
async def save_limit(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    value = parse_amount(message.text or "")
    if value is None:
        await message.answer(_("error.invalid_number"))
        return

    inbox = await users.get_inbox(session, user.id)
    current = await state.get_state()
    if current == InboxSG.daily_limit.state:
        inbox.daily_message_limit = int(value)
    else:
        inbox.per_sender_daily_limit = int(value)
    await session.flush()
    await state.clear()
    await message.answer(_("inbox.limit_saved"))
    await render_inbox(message, session, user, _, edit=False)


# --------------------------------------------------------------------------
# Qo'shimcha sozlamalar
# --------------------------------------------------------------------------


TOGGLES = {
    "first_free": "free_first_message",
    "premium_free": "free_for_premium",
    "refund": "refund_if_no_reply",
}


@router.callback_query(InboxCB.filter(F.action == "extra"))
async def extra_screen(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator
) -> None:
    await _render_extra(query, session, user, _)
    await query.answer()


async def _render_extra(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator
) -> None:
    inbox = await users.get_inbox(session, user.id)
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("inbox.toggle_first_free", state="✅" if inbox.free_first_message else "❌"),
        callback_data=InboxCB(action="toggle", value="first_free"),
    )
    builder.button(
        text=_("inbox.toggle_premium_free", state="✅" if inbox.free_for_premium else "❌"),
        callback_data=InboxCB(action="toggle", value="premium_free"),
    )
    builder.button(
        text=_("inbox.toggle_refund", state="✅" if inbox.refund_if_no_reply else "❌"),
        callback_data=InboxCB(action="toggle", value="refund"),
    )
    builder.button(
        text=_("inbox.hold_hours_btn", hours=inbox.hold_hours),
        callback_data=InboxCB(action="hold"),
    )
    builder.button(text=_("inbox.welcome_btn"), callback_data=InboxCB(action="welcome"))
    builder.button(text=_("common.back"), callback_data=InboxCB(action="home"))
    builder.adjust(1)
    await safe_edit(query, _("inbox.extra_title"), builder.as_markup())


@router.callback_query(InboxCB.filter(F.action == "toggle"))
async def toggle_flag(
    query: CallbackQuery,
    callback_data: InboxCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    field = TOGGLES.get(callback_data.value)
    if field is None:
        await query.answer()
        return
    inbox = await users.get_inbox(session, user.id)
    setattr(inbox, field, not getattr(inbox, field))
    await session.flush()
    await query.answer()
    await _render_extra(query, session, user, _)


@router.callback_query(InboxCB.filter(F.action == "hold"))
async def ask_hold(query: CallbackQuery, state: FSMContext, _: Translator) -> None:
    await state.set_state(InboxSG.hold_hours)
    await safe_edit(query, _("inbox.hold_prompt"), cancel_keyboard(_, "inbox"))
    await query.answer()


@router.message(InboxSG.hold_hours)
async def save_hold(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    value = parse_amount(message.text or "")
    if value is None:
        await message.answer(_("error.invalid_number"))
        return
    inbox = await users.get_inbox(session, user.id)
    inbox.hold_hours = min(int(value), 24 * 30)
    await session.flush()
    await state.clear()
    await message.answer(_("inbox.hold_saved", hours=inbox.hold_hours))
    await render_inbox(message, session, user, _, edit=False)


@router.callback_query(InboxCB.filter(F.action == "welcome"))
async def ask_welcome(query: CallbackQuery, state: FSMContext, _: Translator) -> None:
    await state.set_state(InboxSG.welcome)
    await safe_edit(query, _("inbox.welcome_prompt"), cancel_keyboard(_, "inbox"))
    await query.answer()


@router.message(InboxSG.welcome)
async def save_welcome(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    text = (message.text or "").strip()
    inbox = await users.get_inbox(session, user.id)
    inbox.welcome_text = None if text == "-" else text[:1000]
    await session.flush()
    await state.clear()
    await message.answer(_("inbox.welcome_saved"))
    await render_inbox(message, session, user, _, edit=False)
