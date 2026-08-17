"""Vaqt jadvallari — ham shaxsiy xabarlar, ham guruhlar uchun.

Jadval: hafta kunlari + soat oralig'i → bepul / boshqa narx / yopiq.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ScheduleAction
from bot.db.models import ChatSchedule, ChatSettings, InboxSchedule, User
from bot.handlers.common import make_fmt, parse_price_input, safe_edit
from bot.i18n import Translator
from bot.keyboards.callbacks import GroupCB, InboxCB, SchedCB
from bot.services import users
from bot.states import ScheduleSG
from bot.utils.timeutils import (
    ALL_DAYS,
    DAY_KEYS,
    WEEKDAYS,
    WEEKEND,
    format_hhmm,
    parse_hhmm,
)

logger = logging.getLogger(__name__)

router = Router(name="schedules")

MAX_SCHEDULES = 12

DAY_LABELS = {
    "uz": ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"),
    "ru": ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"),
    "en": ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"),
}


def day_labels(lang: str) -> tuple[str, ...]:
    return DAY_LABELS.get(lang, DAY_LABELS["uz"])


def format_days(mask: int, lang: str) -> str:
    labels = day_labels(lang)
    if mask == ALL_DAYS:
        return {"uz": "Har kuni", "ru": "Каждый день", "en": "Every day"}.get(lang, "Har kuni")
    if mask == WEEKDAYS:
        return f"{labels[0]}–{labels[4]}"
    if mask == WEEKEND:
        return f"{labels[5]}–{labels[6]}"
    return ",".join(labels[i] for i in range(7) if mask & (1 << i)) or "—"


# --------------------------------------------------------------------------
# Ro'yxat
# --------------------------------------------------------------------------


async def _load(session: AsyncSession, scope: str, owner_id: int, chat_id: int):
    if scope == "chat":
        stmt = select(ChatSchedule).where(ChatSchedule.chat_id == chat_id).order_by(
            ChatSchedule.priority, ChatSchedule.id
        )
    else:
        stmt = select(InboxSchedule).where(InboxSchedule.user_id == owner_id).order_by(
            InboxSchedule.priority, InboxSchedule.id
        )
    return list((await session.execute(stmt)).scalars().all())


async def _can_manage(session: AsyncSession, user: User, scope: str, chat_id: int) -> bool:
    if scope != "chat":
        return True
    chat = await session.get(ChatSettings, chat_id)
    return chat is not None and chat.owner_id == user.id


def _back_button(builder: InlineKeyboardBuilder, _: Translator, scope: str, chat_id: int) -> None:
    if scope == "chat":
        builder.button(text=_("common.back"), callback_data=GroupCB(action="open", chat_id=chat_id))
    else:
        builder.button(text=_("common.back"), callback_data=InboxCB(action="home"))


@router.callback_query(SchedCB.filter(F.action == "list"))
async def list_schedules(
    query: CallbackQuery,
    callback_data: SchedCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    await state.clear()
    scope, chat_id = callback_data.scope, callback_data.chat_id

    if not await _can_manage(session, user, scope, chat_id):
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    await _render_list(query, session, user, _, scope, chat_id)
    await query.answer()


async def _render_list(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    scope: str,
    chat_id: int,
) -> None:
    rows = await _load(session, scope, user.id, chat_id)

    if scope == "chat":
        chat = await session.get(ChatSettings, chat_id)
        currency = chat.price_currency if chat else "UZS"
        tz = chat.tz_offset_minutes if chat else user.tz_offset_minutes
    else:
        inbox = await users.get_inbox(session, user.id)
        currency = inbox.price_currency
        tz = user.tz_offset_minutes

    fmt = await make_fmt(session, currency)
    tz_label = f"{'+' if tz >= 0 else '-'}{abs(tz) // 60}"

    lines = [_("inbox.schedule_title", tz=tz_label), ""]
    if not rows:
        lines.append(_("inbox.schedule_empty"))
    else:
        for index, row in enumerate(rows, start=1):
            if row.action == ScheduleAction.PRICE:
                action = fmt(row.price_mxtr)
            elif row.action == ScheduleAction.FREE:
                action = _("inbox.schedule_action_free")
            else:
                action = _("inbox.schedule_action_closed")
            lines.append(
                _(
                    "inbox.schedule_item",
                    index=index,
                    days=format_days(row.days_mask, _.lang),
                    start=format_hhmm(row.start_min),
                    end=format_hhmm(row.end_min),
                    action=action,
                )
            )

    builder = InlineKeyboardBuilder()
    for index, row in enumerate(rows, start=1):
        builder.button(
            text=f"🗑 {index}",
            callback_data=SchedCB(action="del", scope=scope, chat_id=chat_id, item_id=row.id),
        )
    builder.adjust(4)

    builder.row()
    if len(rows) < MAX_SCHEDULES:
        builder.button(
            text=_("inbox.schedule_add"), callback_data=SchedCB(action="add", scope=scope, chat_id=chat_id)
        )
    _back_button(builder, _, scope, chat_id)

    text = "\n".join(lines)
    if isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        await event.answer(text, reply_markup=builder.as_markup())


@router.callback_query(SchedCB.filter(F.action == "del"))
async def delete_schedule(
    query: CallbackQuery,
    callback_data: SchedCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    scope, chat_id = callback_data.scope, callback_data.chat_id
    if not await _can_manage(session, user, scope, chat_id):
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    model = ChatSchedule if scope == "chat" else InboxSchedule
    row = await session.get(model, callback_data.item_id)
    if row is not None:
        owner_ok = (
            row.chat_id == chat_id if scope == "chat" else row.user_id == user.id
        )
        if owner_ok:
            await session.delete(row)
            await session.flush()

    await query.answer(_("inbox.schedule_removed"))
    await _render_list(query, session, user, _, scope, chat_id)


# --------------------------------------------------------------------------
# Qo'shish sehrgari
# --------------------------------------------------------------------------


@router.callback_query(SchedCB.filter(F.action == "add"))
async def add_start(
    query: CallbackQuery,
    callback_data: SchedCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    scope, chat_id = callback_data.scope, callback_data.chat_id
    if not await _can_manage(session, user, scope, chat_id):
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    rows = await _load(session, scope, user.id, chat_id)
    if len(rows) >= MAX_SCHEDULES:
        await query.answer(_("inbox.schedule_limit", max=MAX_SCHEDULES), show_alert=True)
        return

    await state.set_state(ScheduleSG.days)
    await state.update_data(scope=scope, chat_id=chat_id, days_mask=ALL_DAYS)
    await _render_days(query, state, _)
    await query.answer()


async def _render_days(query: CallbackQuery, state: FSMContext, _: Translator) -> None:
    data = await state.get_data()
    mask = int(data.get("days_mask", ALL_DAYS))
    scope, chat_id = data.get("scope", "dm"), int(data.get("chat_id", 0))
    labels = day_labels(_.lang)

    builder = InlineKeyboardBuilder()
    for index, key in enumerate(DAY_KEYS):
        selected = mask & (1 << index)
        builder.button(
            text=f"{'✅' if selected else '▫️'} {labels[index]}",
            callback_data=SchedCB(action="day", scope=scope, chat_id=chat_id, value=key),
        )
    builder.adjust(7)

    builder.row()
    builder.button(
        text=_("inbox.schedule_all_days"),
        callback_data=SchedCB(action="preset", scope=scope, chat_id=chat_id, value="all"),
    )
    builder.button(
        text=_("inbox.schedule_weekdays"),
        callback_data=SchedCB(action="preset", scope=scope, chat_id=chat_id, value="week"),
    )
    builder.button(
        text=_("inbox.schedule_weekend"),
        callback_data=SchedCB(action="preset", scope=scope, chat_id=chat_id, value="end"),
    )
    builder.adjust(7, 3)

    builder.row()
    builder.button(
        text=_("common.next"),
        callback_data=SchedCB(action="days_done", scope=scope, chat_id=chat_id),
    )
    builder.button(
        text=_("common.cancel"),
        callback_data=SchedCB(action="list", scope=scope, chat_id=chat_id),
    )

    await safe_edit(query, _("inbox.schedule_choose_days"), builder.as_markup())


@router.callback_query(SchedCB.filter(F.action == "day"), ScheduleSG.days)
async def toggle_day(
    query: CallbackQuery, callback_data: SchedCB, state: FSMContext, _: Translator
) -> None:
    data = await state.get_data()
    mask = int(data.get("days_mask", 0))
    if callback_data.value in DAY_KEYS:
        mask ^= 1 << DAY_KEYS.index(callback_data.value)
    await state.update_data(days_mask=mask)
    await _render_days(query, state, _)
    await query.answer()


@router.callback_query(SchedCB.filter(F.action == "preset"), ScheduleSG.days)
async def preset_days(
    query: CallbackQuery, callback_data: SchedCB, state: FSMContext, _: Translator
) -> None:
    mask = {"all": ALL_DAYS, "week": WEEKDAYS, "end": WEEKEND}.get(callback_data.value, ALL_DAYS)
    await state.update_data(days_mask=mask)
    await _render_days(query, state, _)
    await query.answer()


@router.callback_query(SchedCB.filter(F.action == "days_done"), ScheduleSG.days)
async def days_done(
    query: CallbackQuery, callback_data: SchedCB, state: FSMContext, _: Translator
) -> None:
    data = await state.get_data()
    if not int(data.get("days_mask", 0)):
        await query.answer(_("inbox.schedule_choose_days"), show_alert=True)
        return

    await state.set_state(ScheduleSG.time)
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("common.cancel"),
        callback_data=SchedCB(action="list", scope=callback_data.scope, chat_id=callback_data.chat_id),
    )
    await safe_edit(query, _("inbox.schedule_time_prompt"), builder.as_markup())
    await query.answer()


@router.message(ScheduleSG.time)
async def receive_time(message: Message, state: FSMContext, _: Translator) -> None:
    raw = (message.text or "").replace("—", "-").replace("–", "-")
    if "-" not in raw:
        await message.answer(_("inbox.schedule_time_invalid"))
        return

    start_raw, _sep, end_raw = raw.partition("-")
    start_min = parse_hhmm(start_raw)
    end_min = parse_hhmm(end_raw)
    if start_min is None or end_min is None or start_min == end_min:
        await message.answer(_("inbox.schedule_time_invalid"))
        return

    data = await state.get_data()
    await state.update_data(start_min=start_min, end_min=end_min)

    scope, chat_id = data.get("scope", "dm"), int(data.get("chat_id", 0))
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("inbox.schedule_action_free"),
        callback_data=SchedCB(action="action", scope=scope, chat_id=chat_id, value=ScheduleAction.FREE),
    )
    builder.button(
        text=_("inbox.schedule_action_price"),
        callback_data=SchedCB(action="action", scope=scope, chat_id=chat_id, value=ScheduleAction.PRICE),
    )
    builder.button(
        text=_("inbox.schedule_action_closed"),
        callback_data=SchedCB(action="action", scope=scope, chat_id=chat_id, value=ScheduleAction.CLOSED),
    )
    builder.adjust(1)
    await message.answer(_("inbox.schedule_choose_action"), reply_markup=builder.as_markup())


@router.callback_query(SchedCB.filter(F.action == "action"), ScheduleSG.time)
async def choose_action(
    query: CallbackQuery,
    callback_data: SchedCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    action = callback_data.value
    if action not in ScheduleAction.ALL:
        await query.answer()
        return

    await state.update_data(action=action)

    if action == ScheduleAction.PRICE:
        await state.set_state(ScheduleSG.price)
        currency = await _currency_for(session, user, callback_data.scope, callback_data.chat_id)
        await safe_edit(query, _("inbox.schedule_price_prompt", currency=_(f"currency.{currency}")))
        await query.answer()
        return

    await _save_schedule(query, state, session, user, _)
    await query.answer(_("inbox.schedule_saved"))


@router.message(ScheduleSG.price)
async def receive_price(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    data = await state.get_data()
    currency = await _currency_for(session, user, data.get("scope", "dm"), int(data.get("chat_id", 0)))
    price_mxtr = await parse_price_input(session, message.text or "", currency)
    if price_mxtr is None:
        await message.answer(_("error.invalid_number"))
        return
    await state.update_data(price_mxtr=price_mxtr)
    await _save_schedule(message, state, session, user, _)


async def _currency_for(
    session: AsyncSession, user: User, scope: str, chat_id: int
) -> str:
    if scope == "chat":
        chat = await session.get(ChatSettings, chat_id)
        return chat.price_currency if chat else "UZS"
    inbox = await users.get_inbox(session, user.id)
    return inbox.price_currency


async def _save_schedule(
    event: CallbackQuery | Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    data = await state.get_data()
    scope = data.get("scope", "dm")
    chat_id = int(data.get("chat_id", 0))

    common = {
        "days_mask": int(data.get("days_mask", ALL_DAYS)),
        "start_min": int(data.get("start_min", 0)),
        "end_min": int(data.get("end_min", 1440)),
        "action": data.get("action", ScheduleAction.PRICE),
        "price_mxtr": int(data.get("price_mxtr", 0)),
    }

    if scope == "chat":
        session.add(ChatSchedule(chat_id=chat_id, **common))
    else:
        session.add(InboxSchedule(user_id=user.id, **common))
    await session.flush()
    await state.clear()

    if isinstance(event, Message):
        await event.answer(_("inbox.schedule_saved"))
    await _render_list(event, session, user, _, scope, chat_id)
