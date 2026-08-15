"""FSM holatlari."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TopupSG(StatesGroup):
    amount = State()


class InboxSG(StatesGroup):
    price = State()
    session_minutes = State()
    hold_hours = State()
    daily_limit = State()
    per_sender_limit = State()
    welcome = State()


class RuleSG(StatesGroup):
    target = State()
    price = State()


class ScheduleSG(StatesGroup):
    days = State()
    time = State()
    price = State()


class RelaySG(StatesGroup):
    """Foydalanuvchi kimgadir yozmoqda."""

    writing = State()
    replying = State()


class GroupSG(StatesGroup):
    price = State()
    quota = State()
    first_free = State()
    content_price = State()


class WithdrawSG(StatesGroup):
    amount = State()
    method = State()
    destination = State()
    holder_name = State()
    confirm = State()


class AdminSG(StatesGroup):
    user_search = State()
    credit = State()
    debit = State()
    ban_reason = State()
    rate_value = State()
    reject_reason = State()
    paid_ref = State()
    broadcast_text = State()
    broadcast_confirm = State()
