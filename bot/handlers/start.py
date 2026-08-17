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
        from bot.services.payments.orders import get_click_url, get_payme_url, create_cryptobot_invoice
        from bot.services import wallet
        from bot.db.models import PaymentOrder
        from sqlalchemy import select
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        order_id = payload[4:]
        order = (await session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))).scalar_one_or_none()
        if not order:
            await message.answer("❌ To'lov buyurtmasi topilmadi yoki muddati o'tgan.")
            return

        price_sum = int(order.amount)
        price_mxtr = int(price_sum / 170 * 1000)
        _total, available_mxtr = await wallet.balance(session, user.id)
        available_sum = int(available_mxtr / 1000 * 170)

        click_url = get_click_url(order.id, price_sum)
        payme_url = get_payme_url(order.id, price_sum * 100)
        crypto_url = await create_cryptobot_invoice(order.id, round(price_sum / 12800, 2))

        buttons = []
        if available_mxtr >= price_mxtr:
            buttons.append([
                InlineKeyboardButton(
                    text=f"✅ Balansdan to'lash ({price_sum:,.0f} so'm)",
                    callback_data=f"paybal:order:{order.id}"
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    text=f"💳 Balansni to'ldirish (Hozir: {available_sum:,.0f} so'm)",
                    callback_data="wallet:topup"
                )
            ])
            buttons.append([
                InlineKeyboardButton(text="🔹 Click orqali to'lash", url=click_url),
                InlineKeyboardButton(text="🟢 Payme orqali to'lash", url=payme_url),
            ])
            if crypto_url:
                buttons.append([InlineKeyboardButton(text="💎 USDT / TON (@CryptoBot)", url=crypto_url)])

        markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(
            f"🔒 <b>Pullik Muloqot To'lovi</b>\n\n"
            f"💰 Narxi: <b>{price_sum:,.0f} so'm</b>\n"
            f"⏱ Ruxsat muddati: <b>24 soat</b>\n"
            f"💵 Sizning balansingiz: <b>{available_sum:,.0f} so'm</b>\n\n"
            f"To'lov usulini tanlang 👇",
            reply_markup=markup,
            parse_mode="HTML",
        )
        return

    # Guruh to'lovi yoki sozlamalari
    if payload.startswith("paygroup_") or (payload.startswith("g_") and payload[2:].lstrip("-").isdigit()) or (payload.startswith("chat_") and payload[5:].lstrip("-").isdigit()):
        from bot.services.payments.orders import get_click_url, get_payme_url, create_cryptobot_invoice, create_payment_order
        from bot.db.models import ChatSettings
        from bot.db.enums import TargetType
        from bot.services import wallet
        from sqlalchemy import select
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        chat_id_raw = payload.split("_", 1)[1]
        try:
            target_chat_id = int(chat_id_raw)
        except ValueError:
            target_chat_id = 0

        chat_row = (await session.execute(select(ChatSettings).where(ChatSettings.chat_id == target_chat_id))).scalar_one_or_none()
        
        # Agar foydalanuvchi guruh egasi bo'lsa va sozlash uchun kirgan bo'lsa
        if chat_row and chat_row.owner_id == user.id and not payload.startswith("paygroup_"):
            from bot.handlers.groups import open_group_card
            await open_group_card(message, session, user, _, fmt, target_chat_id, edit=False)
            return

        if not chat_row:
            await message.answer("❌ Guruh topilmadi yoki bot admin emas.")
            return

        price_mxtr = chat_row.price_mxtr or 29412
        price_sum = int(price_mxtr / 1000 * 170)
        if price_sum < 1000:
            price_sum = 5000

        _total, available_mxtr = await wallet.balance(session, user.id)
        available_sum = int(available_mxtr / 1000 * 170)

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

        buttons = []
        if available_mxtr >= price_mxtr:
            buttons.append([
                InlineKeyboardButton(
                    text=f"✅ Balansdan to'lash ({price_sum:,.0f} so'm)",
                    callback_data=f"paybal:group:{target_chat_id}"
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    text=f"💳 Balansni to'ldirish (Hozir: {available_sum:,.0f} so'm)",
                    callback_data="wallet:topup"
                )
            ])

        buttons.append([
            InlineKeyboardButton(text="🔹 Click orqali to'lash", url=click_url),
            InlineKeyboardButton(text="🟢 Payme orqali to'lash", url=payme_url),
        ])
        if crypto_url:
            buttons.append([InlineKeyboardButton(text="💎 USDT / TON (@CryptoBot)", url=crypto_url)])

        markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(
            f"🔒 <b>Guruhda yozish huquqi: {chat_row.title}</b>\n\n"
            f"💰 Tarif: <b>{price_sum:,.0f} so'm</b> / 30 kun\n"
            f"💵 Sizning balansingiz: <b>{available_sum:,.0f} so'm</b>\n\n"
            f"To'lovni amalga oshirishingiz bilan ushbu guruhdagi yozish joyingiz (input bar) avtomatik ochiladi!\n"
            f"To'lov usulini tanlang 👇",
            reply_markup=markup,
            parse_mode="HTML",
        )
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


@router.callback_query(MenuCB.filter(F.action == "connect_biz"))
async def connect_biz_from_main(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    """Asosiy oynadan Telegram Business orqali 1-klikda kodsiz ulanish."""
    await query.answer()
    is_connected = bool(user.business_enabled and user.business_connection_id)
    status_text = "🟢 <b>Holat: Profilingizga muvaffaqiyatli ulangan!</b>" if is_connected else "⚪️ <b>Holat: Hali ulanmagan</b>"

    bot_user = settings.bot_username or "dofauz_bot"
    text = (
        f"🤖 <b>Telegram Business orqali 1-klikda ulash</b>\n\n"
        f"{status_text}\n\n"
        f"Bu usulda hech qanday telefon raqam yoki SMS kod talab qilinmaydi! 100% rasmiy va xavfsiz.\n\n"
        f"<b>Qanday ulanadi (10 soniya):</b>\n"
        f"1️⃣ Telegram'da <b>Sozlamalar (Настройки)</b>ga kiring.\n"
        f"2️⃣ <b>Telegram Business</b> ➡️ <b>Chat-botlar (Чат-боты)</b> bo'limiga kiring.\n"
        f"3️⃣ Qidiruvga <b>@{bot_user}</b> deb yozing va <b>Ulash (Добавить)</b>ni bosing!\n\n"
        f"✅ Shundan so'ng bot shaxsiy chatlaringizni to'liq himoya qila boshlaydi!"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="💼 Telegram Business ga o'tish", url="tg://settings/business")
    builder.button(text=_("common.back"), callback_data=MenuCB(action="home"))
    builder.adjust(1, 1)
    await safe_edit(query, text, builder.as_markup())


@router.callback_query(MenuCB.filter(F.action == "toggle_bot"))
async def toggle_bot_from_main(
    query: CallbackQuery, session: AsyncSession, user: User, _: Translator, fmt
) -> None:
    """Asosiy menyudan butun bot tizimini to'liq to'xtatish yoki yoqish (Master switch)."""
    user.business_enabled = not bool(user.business_enabled)
    await session.commit()

    if user.business_enabled:
        await query.answer("▶️ Butun bot tizimi yoqildi va faollashtirildi!", show_alert=True)
    else:
        await query.answer("⏸ Butun bot tizimi to'liq to'xtatildi (uzildi)!", show_alert=True)
    await render_main_menu(query, session, user, _, fmt)


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


@router.callback_query(F.data.startswith("paybal:group:"))
async def process_pay_from_balance(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    from datetime import timedelta
    from aiogram.types import ChatPermissions
    from bot.db.models import ActivePermission, ChatSettings
    from bot.db.enums import TargetType
    from bot.services import wallet, chats
    from bot.utils.timeutils import utcnow
    from sqlalchemy import select

    chat_id_str = query.data.split(":")[-1]
    try:
        target_chat_id = int(chat_id_str)
    except ValueError:
        await query.answer("Xatolik yuz berdi", show_alert=True)
        return

    chat_row = (await session.execute(select(ChatSettings).where(ChatSettings.chat_id == target_chat_id))).scalar_one_or_none()
    if not chat_row:
        await query.answer("Guruh topilmadi", show_alert=True)
        return

    price_mxtr = chat_row.price_mxtr or 29412
    price_sum = int(price_mxtr / 1000 * 170)

    _total, available_mxtr = await wallet.balance(session, user.id)
    if available_mxtr < price_mxtr:
        await query.answer("Balansingizda mablag' yetarli emas!", show_alert=True)
        return

    # Balansdan pul yechish va guruh egasiga o'tkazish
    try:
        from bot.db.enums import TxKind
        await wallet.debit(
            session,
            user_id=user.id,
            amount_mxtr=price_mxtr,
            kind=TxKind.CHAT_SPEND,
            chat_id=target_chat_id,
            note=f"Group access: {chat_row.title or target_chat_id}",
        )
        if chat_row.owner_id and chat_row.owner_id != user.id:
            await wallet.credit(
                session,
                user_id=chat_row.owner_id,
                amount_mxtr=price_mxtr,
                kind=TxKind.CHAT_EARN,
                chat_id=target_chat_id,
                counterparty_id=user.id,
                note=f"Group member access: {user.id}",
            )
    except Exception as e:
        logger.error("Balansdan yechishda xatolik: %s", e)

    # Guruhda a'zoga ruxsatnomani ochish
    try:
        await query.bot.restrict_chat_member(
            chat_id=target_chat_id,
            user_id=user.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
            use_independent_chat_permissions=True,
        )
    except Exception as e:
        logger.warning("Guruh a'zosini ochishda xatolik: %s", e)

    # ActivePermission yozish
    now = utcnow()
    perm = ActivePermission(
        target_type=TargetType.GROUP_CHAT,
        owner_id=target_chat_id,
        user_id=user.id,
        expires_at=now + timedelta(days=30),
    )
    session.add(perm)
    await session.commit()

    await query.answer("✅ To'lov muvaffaqiyatli amalga oshirildi!", show_alert=True)
    await safe_edit(
        query,
        f"🎉 <b>To'lov muvaffaqiyatli amalga oshirildi!</b>\n\n"
        f"✅ <b>{chat_row.title}</b> guruhida <b>30 kunlik yozish huquqi</b> faollashtirildi.\n\n"
        f"Guruhga o'tib bemalol yozishingiz mumkin! 🚀",
        None,
    )


@router.callback_query(F.data.startswith("paybal:order:"))
async def process_pay_order_from_balance(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    _: Translator,
    fmt,
) -> None:
    from bot.services import fulfillment
    from bot.db.models import PaymentOrder
    from sqlalchemy import select

    order_id = query.data.split(":")[-1]
    order = (await session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id))).scalar_one_or_none()
    if not order:
        await query.answer("❌ Buyurtma topilmadi", show_alert=True)
        return

    # To'liq bajarish
    success = await fulfillment.fulfill_order(query.bot, session, order)
    if success:
        await query.answer("✅ To'lov muvaffaqiyatli amalga oshirildi!", show_alert=True)
        await safe_edit(
            query,
            f"🎉 <b>To'lov muvaffaqiyatli amalga oshirildi!</b>\n\n"
            f"✅ 24 soatlik erkin muloqot huquqi faollashtirildi.\n"
            f"Shaxsiy chatga o'tib bemalol yozishingiz mumkin! 🚀",
            None,
        )
    else:
        await query.answer("❌ To'lovni amalga oshirishda xatolik yuz berdi", show_alert=True)


@router.callback_query(MenuCB.filter(F.action == "noop"))
async def noop(query: CallbackQuery) -> None:
    await query.answer()

