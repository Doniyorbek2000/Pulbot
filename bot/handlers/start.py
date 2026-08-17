"""/start, deep-link'lar, asosiy menyu, til tanlash va yordam."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.i18n import Translator
from bot.keyboards.callbacks import LangCB, MenuCB
from bot.keyboards.menus import (
    back_to,
    language_keyboard,
    main_menu,
)
from bot.handlers.common import render_main_menu, safe_edit
from bot.services import app_settings, users
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

router = Router(name="start")


@router.message(CommandStart(deep_link=True))
async def start_with_payload(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    """Deep-link bilan kirish: u_<kod> (kimgadir yozish), r_<id> (referal), topup."""
    await state.clear()
    payload = (command.args or "").strip()

    if payload.startswith("pay_"):
        from bot.services.payments.orders import get_click_url, get_payme_url
        from bot.db.models import PaymentOrder
        from sqlalchemy import select
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        order_id = payload[4:]
        order = (await session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))).scalar_one_or_none()
        if not order:
            await message.answer("❌ To'lov buyurtmasi topilmadi yoki muddati o'tgan.")
            return

        click_url = get_click_url(order.id, int(order.amount))
        payme_url = get_payme_url(order.id, int(order.amount * 100))
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔹 Click orqali to'lash", url=click_url)],
                [InlineKeyboardButton(text="🟢 Payme orqali to'lash", url=payme_url)],
            ]
        )
        await message.answer(
            f"💳 <b>To'lov buyurtmasi #{order.id[:8]}</b>\n\n"
            f"Summa: <b>{order.amount:,.0f} {order.currency}</b>\n"
            f"Maqsad: <b>{order.target_type.upper()}</b>\n\n"
            f"To'lovni amalga oshirish uchun quyidagi tugmalardan birini tanlang:",
            reply_markup=markup,
            parse_mode="HTML",
        )
        return

    if payload.startswith("paygroup_"):
        from bot.services.payments.orders import get_click_url, get_payme_url, create_cryptobot_invoice, create_payment_order
        from bot.db.models import ChatSettings, PaymentOrder
        from bot.db.enums import TargetType
        from sqlalchemy import select
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        chat_id_str = payload[9:]
        try:
            target_chat_id = int(chat_id_str)
        except ValueError:
            target_chat_id = 0

        chat_row = (await session.execute(select(ChatSettings).where(ChatSettings.chat_id == target_chat_id))).scalar_one_or_none()
        if not chat_row:
            await message.answer("❌ Guruh topilmadi yoki bot admin emas.")
            return

        price_sum = int(chat_row.price_mxtr / 1000 * 170) if chat_row.price_mxtr else 10000
        if price_sum < 1000:
            price_sum = 10000

        order = await create_payment_order(
            session,
            user_id=user.id,
            recipient_id=chat_row.owner_id,
            target_type=TargetType.GROUP_CHAT,
            target_id=target_chat_id,
            amount=price_sum,
            currency="UZS",
        )
        await session.commit()

        click_url = get_click_url(order.id, price_sum)
        payme_url = get_payme_url(order.id, price_sum * 100)
        crypto_url = await create_cryptobot_invoice(order.id, round(price_sum / 12800, 2))

        buttons = [
            [
                InlineKeyboardButton(text="🔹 Click orqali to'lash", url=click_url),
                InlineKeyboardButton(text="🟢 Payme orqali to'lash", url=payme_url),
            ]
        ]
        if crypto_url:
            buttons.append([InlineKeyboardButton(text="💎 USDT / TON (@CryptoBot)", url=crypto_url)])

        markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(
            f"💳 <b>Guruhda yozish huquqi: {chat_row.title}</b>\n\n"
            f"💰 Tarif: <b>{price_sum:,.0f} so'm</b> / 30 kun\n\n"
            f"To'lovni amalga oshirishingiz bilan ushbu guruhdagi yozish joyingiz (input bar) avtomatik tarzda ochiladi!\n"
            f"To'lov turini tanlang:",
            reply_markup=markup,
            parse_mode="HTML",
        )
        return

    if payload.startswith("chat_") and payload[5:].lstrip("-").isdigit():
        from bot.handlers.groups import open_group_card

        await open_group_card(message, session, user, _, fmt, int(payload[5:]), edit=False)
        return

    if payload.startswith("u_"):
        from bot.handlers.relay import open_compose

        target = await users.by_code(session, payload[2:])
        if target is None:
            await message.answer(_("relay.not_found"))
            await render_main_menu(message, session, user, _, fmt, edit=False)
            return
        await open_compose(message, state, session, user, target, _, fmt)
        return

    if payload == "topup":
        from bot.handlers.wallet import show_topup

        await show_topup(message, session, user, _, fmt, edit=False)
        return

    if payload.startswith("g_") and payload[2:].lstrip("-").isdigit():
        from bot.handlers.groups import open_group_card

        await open_group_card(message, session, user, _, fmt, int(payload[2:]), edit=False)
        return

    await greet(message, session, user, _, fmt)


@router.message(CommandStart())
async def start_plain(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    await state.clear()
    await greet(message, session, user, _, fmt)


async def greet(
    message: Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    """Yangi foydalanuvchidan tilni so'raydi, eskisiga menyuni ko'rsatadi."""
    is_new = (user.last_seen_at - user.created_at).total_seconds() < 3
    if is_new:
        await message.answer(_("language.choose"), reply_markup=language_keyboard())
        return

    await message.answer(_("start.welcome", name=user.first_name or "👤"))
    await render_main_menu(message, session, user, _, fmt, edit=False)


@router.callback_query(LangCB.filter())
async def choose_language(
    query: CallbackQuery,
    callback_data: LangCB,
    session: AsyncSession,
    user: User,
    fmt,
) -> None:
    user.language = callback_data.code
    await session.flush()

    translator = Translator(user.language)
    await query.answer(translator("language.changed"))
    await safe_edit(query, translator("start.welcome", name=user.first_name or "👤"))

    balance_keyboard = main_menu(translator, is_admin=user.is_admin)
    await query.message.answer(
        translator("start.how_it_works"),
        reply_markup=balance_keyboard,
    )


@router.message(Command("til", "language", "lang"))
async def cmd_language(message: Message, _: Translator) -> None:
    await message.answer(_("language.choose"), reply_markup=language_keyboard())


@router.callback_query(MenuCB.filter(F.action == "home"))
async def back_home(
    query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    await state.clear()
    await render_main_menu(query, session, user, _, fmt)
    await query.answer()


@router.message(Command("menu", "asosiy"))
async def cmd_menu(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    await state.clear()
    await render_main_menu(message, session, user, _, fmt, edit=False)


# --------------------------------------------------------------------------
# Havola
# --------------------------------------------------------------------------


@router.callback_query(MenuCB.filter(F.action == "link"))
async def show_link(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    await _render_link(query, session, user, _, fmt)
    await query.answer()


@router.message(Command("havola", "link", "ssylka"))
async def cmd_link(
    message: Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    await _render_link(message, session, user, _, fmt, edit=False)


async def _render_link(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
    *,
    edit: bool = True,
) -> None:
    from bot.keyboards.menus import share_link_keyboard

    inbox = await users.get_inbox(session, user.id)
    link = users.deep_link(user.public_code)
    price = fmt(inbox.price_mxtr) if inbox.price_mxtr else _("common.free")
    text = _("link.title", link=link, price=price, mode=_(f"mode.{inbox.mode}"))
    keyboard = share_link_keyboard(_, link)

    if edit and isinstance(event, CallbackQuery):
        await safe_edit(event, text, keyboard, disable_web_page_preview=True)
    else:
        message = event.message if isinstance(event, CallbackQuery) else event
        await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


# --------------------------------------------------------------------------
# Yordam
# --------------------------------------------------------------------------


@router.callback_query(MenuCB.filter(F.action == "help"))
async def show_help(query: CallbackQuery, session: AsyncSession, _: Translator) -> None:
    await safe_edit(query, await _help_text(session, _), _help_keyboard(_))
    await query.answer()


@router.message(Command("yordam", "help", "pomosh"))
async def cmd_help(message: Message, session: AsyncSession, _: Translator) -> None:
    await message.answer(await _help_text(session, _), reply_markup=_help_keyboard(_))


@router.callback_query(MenuCB.filter(F.action == "faq"))
async def show_faq(query: CallbackQuery, session: AsyncSession, _: Translator) -> None:
    commission = await app_settings.commission_bps(session)
    await safe_edit(
        query,
        _("help.faq", commission=round(commission / 100, 2)),
        back_to(_, "help"),
    )
    await query.answer()


async def _help_text(session: AsyncSession, _: Translator) -> str:
    support = await app_settings.get(session, "support_username", "") or "—"
    if support and not support.startswith("@") and support != "—":
        support = f"@{support}"
    return _("help.text", support=support)


def _help_keyboard(_: Translator):
    builder = InlineKeyboardBuilder()
    builder.button(text=_("help.faq_btn"), callback_data=MenuCB(action="faq"))
    builder.button(text=_("common.back"), callback_data=MenuCB(action="home"))
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(MenuCB.filter(F.action == "noop"))
async def noop(query: CallbackQuery) -> None:
    await query.answer()
