"""Kafolat (escrow): ushlash, javobda yopish, rad etish, muddat tugashi."""

from datetime import timedelta

from bot.db.enums import RelayStatus
from bot.db.models import RelayMessage
from bot.services import relay as relay_service, users, wallet
from bot.utils.money import stars_to_mxtr
from bot.utils.timeutils import utcnow


class FakeBot:
    """Bildirishnomalarni yig'ib boruvchi soxta bot."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return None


async def _held_message(session, sender, recipient, stars: int = 100) -> RelayMessage:
    """Escrow'ga qo'yilgan pullik xabar yaratadi."""
    price = stars_to_mxtr(stars)
    net, fee = price - price // 20, price // 20  # 5%

    relay = RelayMessage(
        sender_id=sender.id,
        recipient_id=recipient.id,
        price_mxtr=price,
        net_mxtr=net,
        commission_mxtr=fee,
        status=RelayStatus.HELD,
        hold_until=utcnow() + timedelta(hours=48),
        preview="salom",
    )
    session.add(relay)
    await session.flush()

    await wallet.hold(
        session, sender.id, price, ref_type="relay", ref_id=relay.id,
        counterparty_id=recipient.id,
    )
    return relay


async def test_hold_takes_money_from_sender_only(session, make_user):
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()

    await _held_message(session, sender, recipient)

    sender_balance, _ = await wallet.balance(session, sender.id)
    recipient_balance, _ = await wallet.balance(session, recipient.id)

    assert sender_balance == stars_to_mxtr(400)
    assert recipient_balance == 0  # hali o'tmagan


async def test_reply_releases_escrow(session, make_user):
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()
    relay = await _held_message(session, sender, recipient)

    earned = await relay_service.settle_on_reply(session, relay)

    assert relay.status == RelayStatus.RELEASED
    assert earned == stars_to_mxtr(95)

    recipient_balance, _ = await wallet.balance(session, recipient.id)
    assert recipient_balance == stars_to_mxtr(95)


async def test_reply_is_idempotent(session, make_user):
    """Ikki marta javob berilsa ham pul bir marta o'tadi."""
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()
    relay = await _held_message(session, sender, recipient)

    await relay_service.settle_on_reply(session, relay)
    await relay_service.settle_on_reply(session, relay)

    recipient_balance, _ = await wallet.balance(session, recipient.id)
    assert recipient_balance == stars_to_mxtr(95)


async def test_reject_refunds_sender(session, make_user):
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()
    relay = await _held_message(session, sender, recipient)

    refunded = await relay_service.reject(session, relay)

    assert relay.status == RelayStatus.REJECTED
    assert refunded == stars_to_mxtr(100)

    sender_balance, _ = await wallet.balance(session, sender.id)
    recipient_balance, _ = await wallet.balance(session, recipient.id)
    assert sender_balance == stars_to_mxtr(500)
    assert recipient_balance == 0


async def test_expired_hold_pays_recipient_by_default(session, make_user):
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()
    relay = await _held_message(session, sender, recipient)
    relay.hold_until = utcnow() - timedelta(minutes=1)
    await session.flush()

    bot = FakeBot()
    released, refunded = await relay_service.expire_holds(bot, session)

    assert (released, refunded) == (1, 0)
    assert relay.status == RelayStatus.RELEASED

    recipient_balance, _ = await wallet.balance(session, recipient.id)
    assert recipient_balance == stars_to_mxtr(95)
    assert bot.sent and bot.sent[0][0] == recipient.id


async def test_expired_hold_refunds_when_configured(session, make_user):
    """`refund_if_no_reply` yoqilgan bo'lsa — javob bo'lmasa pul qaytadi."""
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()

    inbox = await users.get_inbox(session, recipient.id)
    inbox.refund_if_no_reply = True
    await session.flush()

    relay = await _held_message(session, sender, recipient)
    relay.hold_until = utcnow() - timedelta(minutes=1)
    await session.flush()

    bot = FakeBot()
    released, refunded = await relay_service.expire_holds(bot, session)

    assert (released, refunded) == (0, 1)
    assert relay.status == RelayStatus.REFUNDED

    sender_balance, _ = await wallet.balance(session, sender.id)
    assert sender_balance == stars_to_mxtr(500)
    assert bot.sent and bot.sent[0][0] == sender.id


async def test_hold_not_expired_is_untouched(session, make_user):
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()
    relay = await _held_message(session, sender, recipient)

    released, refunded = await relay_service.expire_holds(FakeBot(), session)

    assert (released, refunded) == (0, 0)
    assert relay.status == RelayStatus.HELD


async def test_money_is_conserved(session, make_user):
    """Har qanday yo'l bo'yicha tizimdagi umumiy pul o'zgarmasligi kerak."""
    sender = await make_user(balance_mxtr=stars_to_mxtr(500))
    recipient = await make_user()

    relay = await _held_message(session, sender, recipient)
    await relay_service.settle_on_reply(session, relay)

    sender_balance, _ = await wallet.balance(session, sender.id)
    recipient_balance, _ = await wallet.balance(session, recipient.id)
    commission = relay.commission_mxtr

    assert sender_balance + recipient_balance + commission == stars_to_mxtr(500)
