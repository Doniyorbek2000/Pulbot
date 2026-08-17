"""Callback data fabrikalar (aiogram CallbackData)."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="m"):
    action: str          # home | wallet | inbox | link | groups | withdraw | settings | help | admin


class LangCB(CallbackData, prefix="lang"):
    code: str


class CurrencyCB(CallbackData, prefix="cur"):
    code: str
    scope: str = "user"  # user | inbox | chat


class WalletCB(CallbackData, prefix="w"):
    action: str          # topup | custom | history | invoice | other
    value: int = 0
    page: int = 0


class InboxCB(CallbackData, prefix="ib"):
    action: str          # home | mode | set_mode | price | unit | set_unit | limits | extra
                         # toggle | schedules | rules | welcome | hold | session
    value: str | None = None


class RuleCB(CallbackData, prefix="rl"):
    action: str          # list | add | del | kind
    scope: str = "dm"    # dm | chat
    chat_id: int = 0
    target_id: int = 0
    kind: str | None = None
    page: int = 0


class SchedCB(CallbackData, prefix="sc"):
    action: str          # list | add | day | days_done | action | del | preset
    scope: str = "dm"    # dm | chat
    chat_id: int = 0
    item_id: int = 0
    value: str | None = None


class RelayCB(CallbackData, prefix="r"):
    action: str          # write | pay | cancel | reply | block | refund | profile | session
    target_id: int = 0
    relay_id: int = 0


class GroupCB(CallbackData, prefix="g"):
    action: str          # list | open | toggle | price | quota | first | content | ckind
                         # extra | toggle_flag | owner | schedules | rules
    chat_id: int = 0
    value: str | None = None
    page: int = 0


class WithdrawCB(CallbackData, prefix="wd"):
    action: str          # home | new | method | confirm | cancel | list | item
    item_id: int = 0
    value: str | None = None
    page: int = 0


class AdminCB(CallbackData, prefix="a"):
    action: str          # home | stats | users | withdrawals | rates | settings | broadcast
                         # wd_open | wd_approve | wd_reject | wd_paid | user_open
                         # credit | debit | ban | unban | toggle | set_rate
    value: str | None = None
    item_id: int = 0
    page: int = 0


class SettingsCB(CallbackData, prefix="st"):
    action: str          # home | lang | currency | tz | set_tz | referral
    value: str | None = None


class NoopCB(CallbackData, prefix="noop"):
    """Faqat ko'rsatish uchun tugmalar."""

    tag: str | None = None
