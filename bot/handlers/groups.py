"""Guruh va kanallar: sozlamalar paneli va pullik xabarlarni ushlab qolish."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ChatMode, ContentKind
from bot.db.models import ChatSettings, User
from bot.handlers.common import (
    make_fmt,
    parse_amount,
    parse_price_input,
    safe_edit,
)
from bot.i18n import Translator
from bot.keyboards.callbacks import GroupCB, MenuCB, RuleCB, SchedCB
from bot.keyboards.menus import add_to_group_keyboard, group_topup_keyboard
from bot.services import access, chats, pricing, relay as relay_service, users, wallet
from bot.services.pricing import Reason
from bot.states import GroupSG

logger = logging.getLogger(__name__)

router = Router(name="groups")

GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)

#: Ruxsat berilmagan sabablar uchun ogohlantirish matnlari
DENY_WARN = {
    Reason.RULE_BLOCKED: "group.warn_blocked",
    Reason.NOT_PREMIUM: "group.warn_not_premium",
    Reason.SCHEDULE_CLOSED: "group.warn_closed",
}


# ==========================================================================
# Guruhlar ro'yxati va sozlamalar paneli (shaxsiy chatda)
# ==========================================================================


@router.callback_query(MenuCB.filter(F.action == "groups"))
@router.callback_query(GroupCB.filter(F.action == "list"))
async def list_groups(
    query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    await state.clear()
    rows = await chats.list_for_owner(session, user.id)

    builder = InlineKeyboardBuilder()
    if not rows:
        text = _("group.list_empty")
    else:
        text = _("group.list_title")
        for chat in rows:
            mark = "✅" if chats.is_paid_mode(chat) else "⚪️"
            builder.button(
                text=f"{mark} {chat.title or chat.chat_id}"[:40],
                callback_data=GroupCB(action="open", chat_id=chat.chat_id),
            )
        builder.adjust(1)

    builder.row()
    builder.button(text=_("group.add_btn"), callback_data=GroupCB(action="add"))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="home"))
    builder.adjust(1)

    await safe_edit(query, text, builder.as_markup())
    await query.answer()


@router.callback_query(GroupCB.filter(F.action == "add"))
async def add_group(query: CallbackQuery, _: Translator) -> None:
    await safe_edit(query, _("group.need_admin"), add_to_group_keyboard(_))
    await query.answer()


@router.callback_query(GroupCB.filter(F.action == "open"))
async def open_group(
    query: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    await state.clear()
    await open_group_card(query, session, user, _, fmt, callback_data.chat_id)
    await query.answer()


async def open_group_card(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
    chat_id: int,
    *,
    edit: bool = True,
) -> None:
    chat = await session.get(ChatSettings, chat_id)
    message = event.message if isinstance(event, CallbackQuery) else event

    if chat is None:
        await message.answer(_("error.not_found"))
        return
    if chat.owner_id != user.id and not user.is_admin:
        await message.answer(_("group.not_owner"))
        return

    chat_fmt = await make_fmt(session, chat.price_currency)
    schedules = await chats.count_schedules(session, chat.chat_id)

    status = _("common.enabled") if chats.is_paid_mode(chat) else _("common.disabled")
    if chat.enabled and not chat.bot_can_delete and chat.delete_unpaid:
        status += "\n" + _("group.need_admin")

    text = _(
        "group.title",
        title=chat.title or chat.chat_id,
        status=status,
        mode=_(f"mode.{chat.mode}"),
        price=chat_fmt(chat.price_mxtr) if chat.price_mxtr else _("common.free"),
        quota=chat.free_daily_quota or "—",
        admins="✅" if chat.free_for_admins else "❌",
        premium="✅" if chat.free_for_premium else "❌",
        schedules=schedules,
        earned=chat_fmt(chat.total_earned_mxtr),
    )

    builder = InlineKeyboardBuilder()
    builder.button(text=_("group.toggle"), callback_data=GroupCB(action="toggle", chat_id=chat_id))
    builder.button(text=_("inbox.mode_btn"), callback_data=GroupCB(action="mode", chat_id=chat_id))
    builder.button(text=_("group.price_btn"), callback_data=GroupCB(action="price", chat_id=chat_id))
    builder.button(
        text=_("group.content_price_btn"), callback_data=GroupCB(action="content", chat_id=chat_id)
    )
    builder.button(text=_("group.quota_btn"), callback_data=GroupCB(action="quota", chat_id=chat_id))
    builder.button(
        text=_("group.schedule_btn"),
        callback_data=SchedCB(action="list", scope="chat", chat_id=chat_id),
    )
    builder.button(
        text=_("group.rules_btn"), callback_data=RuleCB(action="list", scope="chat", chat_id=chat_id)
    )
    builder.button(text=_("group.extra_btn"), callback_data=GroupCB(action="extra", chat_id=chat_id))
    builder.button(text=_("common.back"), callback_data=GroupCB(action="list"))
    builder.adjust(2, 2, 2, 2, 1)

    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(GroupCB.filter(F.action == "toggle"))
async def toggle_group(
    query: CallbackQuery,
    callback_data: GroupCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    chat = await _owned_chat(session, user, callback_data.chat_id)
    if chat is None:
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    chat.enabled = not chat.enabled
    if chat.enabled and chat.mode == ChatMode.FREE:
        chat.mode = ChatMode.PAID
    await session.flush()

    await query.answer(_("group.enabled") if chat.enabled else _("group.disabled"))
    await open_group_card(query, session, user, _, fmt, chat.chat_id)


@router.callback_query(GroupCB.filter(F.action == "mode"))
async def group_mode(
    query: CallbackQuery,
    callback_data: GroupCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    chat = await _owned_chat(session, user, callback_data.chat_id)
    if chat is None:
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for mode in ChatMode.ALL:
        mark = "🔘 " if mode == chat.mode else ""
        builder.button(
            text=f"{mark}{_(f'mode.{mode}')}",
            callback_data=GroupCB(action="set_mode", chat_id=chat.chat_id, value=mode),
        )
    builder.button(text=_("common.back"), callback_data=GroupCB(action="open", chat_id=chat.chat_id))
    builder.adjust(1)
    await safe_edit(query, _("inbox.choose_mode", current=_(f"mode.{chat.mode}")), builder.as_markup())
    await query.answer()


@router.callback_query(GroupCB.filter(F.action == "set_mode"))
async def set_group_mode(
    query: CallbackQuery,
    callback_data: GroupCB,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    chat = await _owned_chat(session, user, callback_data.chat_id)
    if chat is None or callback_data.value not in ChatMode.ALL:
        await query.answer(_("error.generic"), show_alert=True)
        return
    chat.mode = callback_data.value
    if chat.mode != ChatMode.FREE:
        chat.enabled = True
    await session.flush()
    await query.answer(_("inbox.mode_saved", mode=_(f"mode.{chat.mode}")))
    await open_group_card(query, session, user, _, fmt, chat.chat_id)


# --------------------------------------------------------------------------
# Narx
# --------------------------------------------------------------------------


@router.callback_query(GroupCB.filter(F.action == "price"))
async def ask_group_price(
    query: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    chat = await _owned_chat(session, user, callback_data.chat_id)
    if chat is None:
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    await state.set_state(GroupSG.price)
    await state.update_data(chat_id=chat.chat_id)

    builder = InlineKeyboardBuilder()
    builder.button(text=_("common.cancel"), callback_data=GroupCB(action="open", chat_id=chat.chat_id))
    await safe_edit(
        query,
        _("group.price_prompt", currency=_(f"currency.{chat.price_currency}")),
        builder.as_markup(),
    )
    await query.answer()


@router.message(GroupSG.price)
async def save_group_price(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    data = await state.get_data()
    chat = await _owned_chat(session, user, int(data.get("chat_id", 0)))
    if chat is None:
        await state.clear()
        await message.answer(_("error.not_found"))
        return

    price_mxtr = await parse_price_input(session, message.text or "", chat.price_currency)
    if price_mxtr is None:
        await message.answer(_("error.invalid_number"))
        return

    chat.price_mxtr = price_mxtr
    if price_mxtr > 0 and chat.mode == ChatMode.FREE:
        chat.mode = ChatMode.PAID
        chat.enabled = True
    await session.flush()
    await state.clear()

    chat_fmt = await make_fmt(session, chat.price_currency)
    await message.answer(_("cmd.price_set", price=chat_fmt(price_mxtr)))
    await open_group_card(message, session, user, _, fmt, chat.chat_id, edit=False)


# --------------------------------------------------------------------------
# Kontent turi bo'yicha narx
# --------------------------------------------------------------------------


@router.callback_query(GroupCB.filter(F.action == "content"))
async def content_prices(
    query: CallbackQuery,
    callback_data: GroupCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    chat = await _owned_chat(session, user, callback_data.chat_id)
    if chat is None:
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    chat_fmt = await make_fmt(session, chat.price_currency)
    prices = chat.price_by_content or {}

    builder = InlineKeyboardBuilder()
    for kind in ContentKind.ALL:
        label = _(f"group.content_kind.{kind}")
        value = prices.get(kind)
        suffix = chat_fmt(int(value)) if value is not None else "—"
        builder.button(
            text=f"{label}: {suffix}",
            callback_data=GroupCB(action="ckind", chat_id=chat.chat_id, value=kind),
        )
    builder.button(text=_("common.back"), callback_data=GroupCB(action="open", chat_id=chat.chat_id))
    builder.adjust(2, 2, 2, 2, 2, 1)

    await safe_edit(query, _("group.content_title"), builder.as_markup())
    await query.answer()


@router.callback_query(GroupCB.filter(F.action == "ckind"))
async def ask_content_price(
    query: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    chat = await _owned_chat(session, user, callback_data.chat_id)
    if chat is None:
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    await state.set_state(GroupSG.content_price)
    await state.update_data(chat_id=chat.chat_id, kind=callback_data.value)

    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("common.cancel"), callback_data=GroupCB(action="content", chat_id=chat.chat_id)
    )
    await safe_edit(
        query,
        _(
            "group.content_prompt",
            kind=_(f"group.content_kind.{callback_data.value}"),
            currency=_(f"currency.{chat.price_currency}"),
        ),
        builder.as_markup(),
    )
    await query.answer()


@router.message(GroupSG.content_price)
async def save_content_price(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    data = await state.get_data()
    chat = await _owned_chat(session, user, int(data.get("chat_id", 0)))
    kind = data.get("kind", "")
    if chat is None or kind not in ContentKind.ALL:
        await state.clear()
        await message.answer(_("error.not_found"))
        return

    raw = (message.text or "").strip()
    prices = dict(chat.price_by_content or {})

    if raw == "-":
        prices.pop(kind, None)
    else:
        price_mxtr = await parse_price_input(session, raw, chat.price_currency)
        if price_mxtr is None:
            await message.answer(_("error.invalid_number"))
            return
        prices[kind] = price_mxtr

    chat.price_by_content = prices or None
    await session.flush()
    await state.clear()
    await message.answer(_("common.done"))
    await open_group_card(message, session, user, _, fmt, chat.chat_id, edit=False)


# --------------------------------------------------------------------------
# Bepul kvotalar va qo'shimchalar
# --------------------------------------------------------------------------


@router.callback_query(GroupCB.filter(F.action == "quota"))
async def ask_quota(
    query: CallbackQuery,
    callback_data: GroupCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    chat = await _owned_chat(session, user, callback_data.chat_id)
    if chat is None:
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    await state.set_state(GroupSG.quota)
    await state.update_data(chat_id=chat.chat_id)

    builder = InlineKeyboardBuilder()
    builder.button(text=_("common.cancel"), callback_data=GroupCB(action="open", chat_id=chat.chat_id))
    await safe_edit(query, _("group.quota_prompt"), builder.as_markup())
    await query.answer()


@router.message(GroupSG.quota)
async def save_quota(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    data = await state.get_data()
    chat = await _owned_chat(session, user, int(data.get("chat_id", 0)))
    value = parse_amount(message.text or "")
    if chat is None or value is None:
        await message.answer(_("error.invalid_number"))
        return

    chat.free_daily_quota = int(value)
    await session.flush()
    await state.clear()
    await message.answer(_("inbox.limit_saved"))
    await open_group_card(message, session, user, _, fmt, chat.chat_id, edit=False)


GROUP_TOGGLES = {
    "delete": "delete_unpaid",
    "warn": "warn_unpaid",
    "admins": "free_for_admins",
    "premium": "free_for_premium",
}


@router.callback_query(GroupCB.filter(F.action == "extra"))
async def group_extra(
    query: CallbackQuery,
    callback_data: GroupCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    chat = await _owned_chat(session, user, callback_data.chat_id)
    if chat is None:
        await query.answer(_("group.not_owner"), show_alert=True)
        return
    await _render_extra(query, chat, _)
    await query.answer()


async def _render_extra(query: CallbackQuery, chat: ChatSettings, _: Translator) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("group.toggle_delete", state="✅" if chat.delete_unpaid else "❌"),
        callback_data=GroupCB(action="flag", chat_id=chat.chat_id, value="delete"),
    )
    builder.button(
        text=_("group.toggle_warn", state="✅" if chat.warn_unpaid else "❌"),
        callback_data=GroupCB(action="flag", chat_id=chat.chat_id, value="warn"),
    )
    builder.button(
        text=_("group.toggle_admins", state="✅" if chat.free_for_admins else "❌"),
        callback_data=GroupCB(action="flag", chat_id=chat.chat_id, value="admins"),
    )
    builder.button(
        text=_("group.toggle_premium", state="✅" if chat.free_for_premium else "❌"),
        callback_data=GroupCB(action="flag", chat_id=chat.chat_id, value="premium"),
    )
    builder.button(text=_("common.back"), callback_data=GroupCB(action="open", chat_id=chat.chat_id))
    builder.adjust(1)
    await safe_edit(query, _("inbox.extra_title"), builder.as_markup())


@router.callback_query(GroupCB.filter(F.action == "flag"))
async def toggle_group_flag(
    query: CallbackQuery,
    callback_data: GroupCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    chat = await _owned_chat(session, user, callback_data.chat_id)
    field = GROUP_TOGGLES.get(callback_data.value)
    if chat is None or field is None:
        await query.answer(_("error.generic"), show_alert=True)
        return
    setattr(chat, field, not getattr(chat, field))
    await session.flush()
    await query.answer()
    await _render_extra(query, chat, _)


async def _owned_chat(session: AsyncSession, user: User, chat_id: int) -> ChatSettings | None:
    chat = await session.get(ChatSettings, chat_id)
    if chat is None:
        return None
    if chat.owner_id != user.id and not user.is_admin:
        return None
    return chat


# ==========================================================================
# Guruhdagi hodisalar
# ==========================================================================


@router.my_chat_member()
async def bot_membership_changed(
    event: ChatMemberUpdated, session: AsyncSession
) -> None:
    """Bot guruhga qo'shilganda/adminlik huquqi o'zgarganda."""
    if event.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        return

    status = event.new_chat_member.status
    if status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        chat = await session.get(ChatSettings, event.chat.id)
        if chat is not None:
            chat.enabled = False
            chat.bot_can_delete = False
            await session.flush()
        return

    actor = await users.get_or_create(session, event.from_user)
    chat = await chats.get_or_create(session, event.chat, owner_id=actor.id)
    chat.bot_can_delete = bool(
        status == ChatMemberStatus.ADMINISTRATOR
        and getattr(event.new_chat_member, "can_delete_messages", False)
    )
    await session.flush()

    translator = Translator(actor.language)
    from bot.handlers.common import notify
    from bot.config import settings as app_config

    text = translator("group.added", title=chat.title or chat.chat_id)
    if not chat.bot_can_delete:
        text += "\n\n" + translator("group.need_admin")

    builder = InlineKeyboardBuilder()
    builder.button(
        text=translator("menu.groups"),
        url=f"https://t.me/{app_config.bot_username}?start=g_{chat.chat_id}",
    )
    await notify(event.bot, actor.id, text, builder.as_markup())


@router.message(Command("sozlash", "setup"), F.chat.type.in_(GROUP_TYPES))
async def group_setup(
    message: Message, session: AsyncSession, user: User, _: Translator
) -> None:
    """Guruhda sozlash — botga havola beradi (sozlamalar shaxsiy chatda)."""
    from bot.config import settings as app_config

    member = await message.bot.get_chat_member(message.chat.id, user.id)
    if member.status not in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        await message.reply(_("cmd.admin_only"))
        return

    chat = await chats.get_or_create(session, message.chat, owner_id=user.id)
    if chat.owner_id is None:
        chat.owner_id = user.id
        await session.flush()

    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("menu.groups"),
        url=f"https://t.me/{app_config.bot_username}?start=g_{chat.chat_id}",
    )
    await message.reply(_("group.added", title=chat.title or ""), reply_markup=builder.as_markup())


@router.message(Command("narx", "price"), F.chat.type.in_(GROUP_TYPES))
async def group_price_command(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    member = await message.bot.get_chat_member(message.chat.id, user.id)
    if member.status not in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        await message.reply(_("cmd.admin_only"))
        return

    chat = await chats.get_or_create(session, message.chat, owner_id=user.id)
    if not command.args:
        await message.reply(_("cmd.price_usage"))
        return

    price_mxtr = await parse_price_input(session, command.args, chat.price_currency)
    if price_mxtr is None:
        await message.reply(_("cmd.price_usage"))
        return

    chat.price_mxtr = price_mxtr
    if price_mxtr > 0:
        chat.enabled = True
        if chat.mode == ChatMode.FREE:
            chat.mode = ChatMode.PAID
    await session.flush()

    chat_fmt = await make_fmt(session, chat.price_currency)
    await message.reply(_("cmd.price_set", price=chat_fmt(price_mxtr)))


@router.message(Command("bepul", "free"), F.chat.type.in_(GROUP_TYPES))
async def group_free_command(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    from bot.db.enums import AccessRuleKind

    member = await message.bot.get_chat_member(message.chat.id, user.id)
    if member.status not in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        await message.reply(_("cmd.admin_only"))
        return

    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = await users.get_or_create(session, message.reply_to_message.from_user)
    elif command.args:
        target = await users.resolve(session, command.args)

    if target is None:
        await message.reply(_("cmd.free_usage"))
        return

    await access.set_rule(
        session, target.id, AccessRuleKind.FREE, chat_id=message.chat.id
    )
    await message.reply(
        _("inbox.rule_saved", name=target.mention, kind=_("inbox.rule_kind.free"))
    )


@router.message(Command("bloklash", "block"), F.chat.type.in_(GROUP_TYPES))
async def group_block_command(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    from bot.db.enums import AccessRuleKind

    member = await message.bot.get_chat_member(message.chat.id, user.id)
    if member.status not in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        await message.reply(_("cmd.admin_only"))
        return

    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = await users.get_or_create(session, message.reply_to_message.from_user)
    elif command.args:
        target = await users.resolve(session, command.args)

    if target is None:
        await message.reply(_("cmd.block_usage"))
        return

    await access.set_rule(
        session, target.id, AccessRuleKind.BLOCKED, chat_id=message.chat.id
    )
    await message.reply(
        _("inbox.rule_saved", name=target.mention, kind=_("inbox.rule_kind.blocked"))
    )


# --------------------------------------------------------------------------
# Pullik xabarlarni ushlab qolish
# --------------------------------------------------------------------------


@router.message(F.chat.type.in_(GROUP_TYPES))
async def charge_group_message(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    """Guruhdagi har bir xabarni tekshiradi va kerak bo'lsa pul yechadi."""
    chat = await session.get(ChatSettings, message.chat.id)
    if chat is None or not chats.is_paid_mode(chat):
        return
    if message.from_user is None or message.from_user.is_bot:
        return

    # Guruh adminimi?
    is_admin = False
    if chat.free_for_admins:
        try:
            member = await bot.get_chat_member(message.chat.id, user.id)
            is_admin = member.status in (
                ChatMemberStatus.CREATOR,
                ChatMemberStatus.ADMINISTRATOR,
            )
        except TelegramAPIError:
            is_admin = False

    content_kind = relay_service.detect_content_kind(message)
    quote = await pricing.quote_chat(
        session, user, chat, content_kind=content_kind, is_chat_admin=is_admin
    )

    if not quote.allowed:
        await _reject_message(bot, message, chat, _, DENY_WARN.get(quote.reason, "group.warn_blocked"))
        return

    if quote.price_mxtr <= 0:
        await chats.charge_for_message(
            session, chat=chat, sender=user, quote=quote, message_id=message.message_id
        )
        return

    paid, charged = await chats.charge_for_message(
        session, chat=chat, sender=user, quote=quote, message_id=message.message_id
    )

    if paid:
        return

    # Mablag' yetmadi
    _total, available = await wallet.balance(session, user.id)
    chat_fmt = await make_fmt(session, chat.price_currency)
    warning = _(
        "group.warn_unpaid",
        name=user.mention,
        price=chat_fmt(quote.price_mxtr),
        balance=fmt(available),
    )
    await _reject_message(bot, message, chat, _, None, custom_text=warning, with_topup=True)


async def _reject_message(
    bot: Bot,
    message: Message,
    chat: ChatSettings,
    _: Translator,
    warn_key: str | None,
    *,
    custom_text: str | None = None,
    with_topup: bool = False,
) -> None:
    """To'lanmagan xabarni o'chiradi va ogohlantiradi."""
    if chat.delete_unpaid and chat.bot_can_delete:
        try:
            await message.delete()
        except TelegramAPIError as exc:
            logger.debug("Xabarni o'chirib bo'lmadi (%s): %s", chat.chat_id, exc)

    if not chat.warn_unpaid:
        return

    text = custom_text or _(warn_key or "group.warn_blocked", name=message.from_user.full_name)
    keyboard = group_topup_keyboard(_) if with_topup else None

    try:
        warning = await bot.send_message(message.chat.id, text, reply_markup=keyboard)
    except TelegramAPIError:
        return

    # Ogohlantirish guruhni to'ldirmasligi uchun avtomatik o'chiriladi
    async def _cleanup() -> None:
        await asyncio.sleep(max(5, chat.warn_ttl_seconds))
        try:
            await warning.delete()
        except TelegramAPIError:
            pass

    asyncio.create_task(_cleanup())  # noqa: RUF006
