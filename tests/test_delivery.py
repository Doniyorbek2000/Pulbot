"""To'liq yetkazish oqimi: narx → to'lov → yetkazish → javob → tushum."""

from datetime import datetime, timezone

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import Chat, Message

from bot.db.enums import InboxMode, RelayStatus
from bot.services import pricing, relay as relay_service, users, wallet
from bot.utils.money import stars_to_mxtr


class FakeBot:
    """send_message / copy_message ni yozib boruvchi soxta bot."""

    def __init__(self, *, forbidden: bool = False) -> None:
        self.forbidden = forbidden
        self.messages: list[dict] = []
        self._next_id = 500

    async def send_message(self, chat_id, text, **kwargs):
        if self.forbidden:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        self._next_id += 1
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return Message(
            message_id=self._next_id,
            date=datetime.now(tz=timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            text=text,
        )

    async def copy_message(self, chat_id, from_chat_id, message_id, **kwargs):
        self._next_id += 1
        self.messages.append({"chat_id": chat_id, "copied_from": message_id})

        class _Copied:
            pass

        copied = _Copied()
        copied.message_id = self._next_id
        return copied


def make_message(user_id: int, text: str = "Salom, savolim bor") -> Message:
    return Message(
        message_id=42,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=user_id, type="private"),
        text=text,
    )


async def _setup(session, make_user, *, price_stars: int = 100, hold_hours: int = 48):
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()
    inbox = await users.get_inbox(session, recipient.id)
    inbox.mode = InboxMode.PAID
    inbox.price_mxtr = stars_to_mxtr(price_stars)
    inbox.hold_hours = hold_hours
    await session.flush()
    return sender, recipient, inbox


async def test_paid_delivery_with_escrow(session, make_user):
    sender, recipient, inbox = await _setup(session, make_user)
    quote = await pricing.quote_dm(session, sender, recipient, inbox)
    bot = FakeBot()

    result = await relay_service.deliver(
        bot, session,
        sender=sender, recipient=recipient, inbox=inbox, quote=quote,
        message=make_message(sender.id),
    )

    assert result.charged_mxtr == stars_to_mxtr(100)
    assert result.held is True
    assert result.relay.status == RelayStatus.HELD
    assert result.relay.delivered_message_id is not None

    # Xabar qabul qiluvchiga yetdi
    assert bot.messages and bot.messages[0]["chat_id"] == recipient.id
    assert "Salom, savolim bor" in bot.messages[0]["text"]

    # Pul yechildi, lekin hali o'tmadi
    sender_balance, _ = await wallet.balance(session, sender.id)
    recipient_balance, _ = await wallet.balance(session, recipient.id)
    assert sender_balance == stars_to_mxtr(400)
    assert recipient_balance == 0


async def test_delivery_without_escrow_pays_immediately(session, make_user):
    sender, recipient, inbox = await _setup(session, make_user, hold_hours=0)
    quote = await pricing.quote_dm(session, sender, recipient, inbox)

    result = await relay_service.deliver(
        FakeBot(), session,
        sender=sender, recipient=recipient, inbox=inbox, quote=quote,
        message=make_message(sender.id),
    )

    assert result.held is False
    assert result.relay.status == RelayStatus.RELEASED

    recipient_balance, _ = await wallet.balance(session, recipient.id)
    assert recipient_balance == stars_to_mxtr(95)  # 5% komissiya


async def test_blocked_recipient_gets_refund(session, make_user):
    """Qabul qiluvchi botni bloklagan bo'lsa — pul yuboruvchiga qaytadi."""
    sender, recipient, inbox = await _setup(session, make_user)
    quote = await pricing.quote_dm(session, sender, recipient, inbox)

    with pytest.raises(relay_service.DeliveryError):
        await relay_service.deliver(
            FakeBot(forbidden=True), session,
            sender=sender, recipient=recipient, inbox=inbox, quote=quote,
            message=make_message(sender.id),
        )

    sender_balance, _ = await wallet.balance(session, sender.id)
    assert sender_balance == stars_to_mxtr(500)  # pul to'liq qaytdi
    assert recipient.bot_blocked is True


async def test_free_delivery_costs_nothing(session, make_user):
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()
    inbox = await users.get_inbox(session, recipient.id)

    quote = await pricing.quote_dm(session, sender, recipient, inbox)
    result = await relay_service.deliver(
        FakeBot(), session,
        sender=sender, recipient=recipient, inbox=inbox, quote=quote,
        message=make_message(sender.id),
    )

    assert result.charged_mxtr == 0
    assert result.relay.status == RelayStatus.FREE

    sender_balance, _ = await wallet.balance(session, sender.id)
    assert sender_balance == stars_to_mxtr(500)


async def test_session_mode_charges_once(session, make_user):
    """Sessiya rejimida birinchi xabar to'lanadi, keyingilari bepul."""
    sender, recipient, inbox = await _setup(session, make_user, price_stars=50)
    inbox.pricing_unit = "per_session"
    inbox.session_minutes = 60
    await session.flush()

    bot = FakeBot()

    first_quote = await pricing.quote_dm(session, sender, recipient, inbox)
    assert first_quote.price_mxtr == stars_to_mxtr(50)

    first = await relay_service.deliver(
        bot, session, sender=sender, recipient=recipient, inbox=inbox,
        quote=first_quote, message=make_message(sender.id),
    )
    assert first.session_started is True

    # Ikkinchi xabar — sessiya faol, pul olinmaydi
    second_quote = await pricing.quote_dm(session, sender, recipient, inbox)
    assert second_quote.is_free
    assert second_quote.reason == pricing.Reason.SESSION_ACTIVE

    await relay_service.deliver(
        bot, session, sender=sender, recipient=recipient, inbox=inbox,
        quote=second_quote, message=make_message(sender.id, "yana savol"),
    )

    sender_balance, _ = await wallet.balance(session, sender.id)
    assert sender_balance == stars_to_mxtr(450)  # faqat bir marta yechilgan


async def test_reply_lookup_by_delivered_message(session, make_user):
    """Yetkazilgan xabarga reply qilinganda kerakli yozuv topilishi kerak."""
    sender, recipient, inbox = await _setup(session, make_user)
    quote = await pricing.quote_dm(session, sender, recipient, inbox)
    bot = FakeBot()

    result = await relay_service.deliver(
        bot, session, sender=sender, recipient=recipient, inbox=inbox,
        quote=quote, message=make_message(sender.id),
    )

    found = await relay_service.find_by_delivered_message(
        session, recipient.id, result.relay.delivered_message_id
    )
    assert found is not None and found.id == result.relay.id

    # Javob berilgach escrow yopiladi
    earned = await relay_service.settle_on_reply(session, found)
    assert earned == stars_to_mxtr(95)


async def test_daily_counter_increases(session, make_user):
    from bot.services import access

    sender, recipient, inbox = await _setup(session, make_user, hold_hours=0)
    quote = await pricing.quote_dm(session, sender, recipient, inbox)

    await relay_service.deliver(
        FakeBot(), session, sender=sender, recipient=recipient, inbox=inbox,
        quote=quote, message=make_message(sender.id),
    )

    total, per_sender = await access.inbox_counters(
        session, recipient.id, sender.id, recipient.tz_offset_minutes
    )
    assert total == 1 and per_sender == 1
