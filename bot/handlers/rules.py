"""Istisnolar: bepul yozadiganlar, bloklanganlar, alohida narxlar.

Ham shaxsiy xabarlar (scope="dm"), ham guruhlar (scope="chat") uchun.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import AccessRuleKind
from bot.db.models import ChatSettings, User
from bot.handlers.common import make_fmt, parse_price_input, safe_edit
from bot.i18n import Translator
from bot.keyboards.callbacks import GroupCB, InboxCB, RuleCB
from bot.services import access, users
from bot.states import RuleSG

logger = logging.getLogger(__name__)

router = Router(name="rules")

PAGE_SIZE = 10


async def _can_manage(session: AsyncSession, user: User, scope: str, chat_id: int) -> bool:
    if scope != "chat":
        return True
    chat = await session.get(ChatSettings, chat_id)
    return chat is not None and chat.owner_id == user.id


async def _rule_scope(user: User, scope: str, chat_id: int) -> dict:
    """Qoida qaysi doiraga tegishli: DM uchun owner_id, guruh uchun chat_id."""
    if scope == "chat":
        return {"owner_id": 0, "chat_id": chat_id}
    return {"owner_id": user.id, "chat_id": 0}


@router.callback_query(RuleCB.filter(F.action == "list"))
async def list_rules(
    query: CallbackQuery,
    callback_data: RuleCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    await state.clear()
    if not await _can_manage(session, user, callback_data.scope, callback_data.chat_id):
        await query.answer(_("group.not_owner"), show_alert=True)
        return
    await _render_list(
        query, session, user, _, callback_data.scope, callback_data.chat_id, callback_data.page
    )
    await query.answer()


async def _render_list(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    scope: str,
    chat_id: int,
    page: int = 0,
) -> None:
    scope_args = await _rule_scope(user, scope, chat_id)
    rules = await access.list_rules(
        session, **scope_args, limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE
    )
    has_next = len(rules) > PAGE_SIZE
    rules = rules[:PAGE_SIZE]

    if scope == "chat":
        chat = await session.get(ChatSettings, chat_id)
        currency = chat.price_currency if chat else "UZS"
    else:
        inbox = await users.get_inbox(session, user.id)
        currency = inbox.price_currency
    fmt = await make_fmt(session, currency)

    lines = [_("inbox.rules_title"), ""]
    builder = InlineKeyboardBuilder()

    if not rules:
        lines.append(_("inbox.rules_empty"))
    else:
        for rule in rules:
            target = await session.get(User, rule.target_id)
            name = target.mention if target else f"#{rule.target_id}"
            kind_label = _(
                f"inbox.rule_kind.{rule.kind}",
                price=fmt(rule.price_mxtr) if rule.kind == AccessRuleKind.CUSTOM_PRICE else "",
            )
            lines.append(f"• {name} — {kind_label}")
            builder.button(
                text=f"🗑 {name}"[:24],
                callback_data=RuleCB(
                    action="del", scope=scope, chat_id=chat_id, target_id=rule.target_id
                ),
            )
        builder.adjust(2)

    builder.row()
    builder.button(
        text=_("inbox.rule_add_free"),
        callback_data=RuleCB(action="add", scope=scope, chat_id=chat_id, kind=AccessRuleKind.FREE),
    )
    builder.button(
        text=_("inbox.rule_add_price"),
        callback_data=RuleCB(
            action="add", scope=scope, chat_id=chat_id, kind=AccessRuleKind.CUSTOM_PRICE
        ),
    )
    builder.button(
        text=_("inbox.rule_add_block"),
        callback_data=RuleCB(action="add", scope=scope, chat_id=chat_id, kind=AccessRuleKind.BLOCKED),
    )
    builder.adjust(2, 1)

    builder.row()
    if page > 0:
        builder.button(
            text=_("common.prev"),
            callback_data=RuleCB(action="list", scope=scope, chat_id=chat_id, page=page - 1),
        )
    if has_next:
        builder.button(
            text=_("common.next"),
            callback_data=RuleCB(action="list", scope=scope, chat_id=chat_id, page=page + 1),
        )

    builder.row()
    if scope == "chat":
        builder.button(text=_("common.back"), callback_data=GroupCB(action="open", chat_id=chat_id))
    else:
        builder.button(text=_("common.back"), callback_data=InboxCB(action="home"))

    text = "\n".join(lines)
    if isinstance(event, CallbackQuery):
        await safe_edit(event, text, builder.as_markup())
    else:
        await event.answer(text, reply_markup=builder.as_markup())


@router.callback_query(RuleCB.filter(F.action == "del"))
async def delete_rule(
    query: CallbackQuery,
    callback_data: RuleCB,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    if not await _can_manage(session, user, callback_data.scope, callback_data.chat_id):
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    scope_args = await _rule_scope(user, callback_data.scope, callback_data.chat_id)
    await access.remove_rule(session, callback_data.target_id, **scope_args)
    await query.answer(_("inbox.rule_removed"))
    await _render_list(query, session, user, _, callback_data.scope, callback_data.chat_id)


@router.callback_query(RuleCB.filter(F.action == "add"))
async def add_rule(
    query: CallbackQuery,
    callback_data: RuleCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    if not await _can_manage(session, user, callback_data.scope, callback_data.chat_id):
        await query.answer(_("group.not_owner"), show_alert=True)
        return

    await state.set_state(RuleSG.target)
    await state.update_data(
        scope=callback_data.scope, chat_id=callback_data.chat_id, kind=callback_data.kind
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("common.cancel"),
        callback_data=RuleCB(action="list", scope=callback_data.scope, chat_id=callback_data.chat_id),
    )
    await safe_edit(query, _("inbox.rule_prompt_user"), builder.as_markup())
    await query.answer()


@router.message(RuleSG.target)
async def receive_target(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    target = await _extract_user(session, message)
    if target is None:
        await message.answer(_("cmd.user_not_found"))
        return
    if target.id == user.id:
        await message.answer(_("error.generic"))
        return

    data = await state.get_data()
    await state.update_data(target_id=target.id)
    kind = data.get("kind", AccessRuleKind.FREE)

    if kind == AccessRuleKind.CUSTOM_PRICE:
        currency = await _currency(session, user, data.get("scope", "dm"), int(data.get("chat_id", 0)))
        await state.set_state(RuleSG.price)
        await message.answer(_("inbox.rule_prompt_price", currency=_(f"currency.{currency}")))
        return

    await _persist(message, state, session, user, _, target, kind, 0)


@router.message(RuleSG.price)
async def receive_price(
    message: Message, state: FSMContext, session: AsyncSession, user: User, _: Translator
) -> None:
    data = await state.get_data()
    currency = await _currency(session, user, data.get("scope", "dm"), int(data.get("chat_id", 0)))
    price_mxtr = await parse_price_input(session, message.text or "", currency)
    if price_mxtr is None:
        await message.answer(_("error.invalid_number"))
        return

    target = await session.get(User, int(data.get("target_id", 0)))
    if target is None:
        await state.clear()
        await message.answer(_("cmd.user_not_found"))
        return

    await _persist(message, state, session, user, _, target, AccessRuleKind.CUSTOM_PRICE, price_mxtr)


async def _persist(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    target: User,
    kind: str,
    price_mxtr: int,
) -> None:
    data = await state.get_data()
    scope = data.get("scope", "dm")
    chat_id = int(data.get("chat_id", 0))
    scope_args = await _rule_scope(user, scope, chat_id)

    await access.set_rule(session, target.id, kind, **scope_args, price_mxtr=price_mxtr)
    await state.clear()

    currency = await _currency(session, user, scope, chat_id)
    fmt = await make_fmt(session, currency)
    await message.answer(
        _(
            "inbox.rule_saved",
            name=target.mention,
            kind=_(f"inbox.rule_kind.{kind}", price=fmt(price_mxtr)),
        )
    )
    await _render_list(message, session, user, _, scope, chat_id)


async def _currency(session: AsyncSession, user: User, scope: str, chat_id: int) -> str:
    if scope == "chat":
        chat = await session.get(ChatSettings, chat_id)
        return chat.price_currency if chat else "UZS"
    inbox = await users.get_inbox(session, user.id)
    return inbox.price_currency


async def _extract_user(session: AsyncSession, message: Message) -> User | None:
    """Forward, @username yoki ID dan foydalanuvchini aniqlaydi."""
    origin = message.forward_origin
    if origin is not None and getattr(origin, "sender_user", None) is not None:
        return await users.get_or_create(session, origin.sender_user)
    return await users.resolve(session, message.text or "")


# --------------------------------------------------------------------------
# Tez buyruqlar (shaxsiy chatda)
# --------------------------------------------------------------------------


@router.message(Command("bepul", "free"), F.chat.type == "private")
async def cmd_free(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    if not command.args:
        await message.answer(_("cmd.free_usage"))
        return
    target = await users.resolve(session, command.args)
    if target is None:
        await message.answer(_("cmd.user_not_found"))
        return
    await access.set_rule(session, target.id, AccessRuleKind.FREE, owner_id=user.id)
    await message.answer(
        _("inbox.rule_saved", name=target.mention, kind=_("inbox.rule_kind.free"))
    )


@router.message(Command("bloklash", "block"), F.chat.type == "private")
async def cmd_block(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    _: Translator,
) -> None:
    if not command.args:
        await message.answer(_("cmd.block_usage"))
        return
    target = await users.resolve(session, command.args)
    if target is None:
        await message.answer(_("cmd.user_not_found"))
        return
    await access.set_rule(session, target.id, AccessRuleKind.BLOCKED, owner_id=user.id)
    await message.answer(
        _("inbox.rule_saved", name=target.mention, kind=_("inbox.rule_kind.blocked"))
    )
