"""To'lov shlyuzlari va fulfillment sinovlari."""

import pytest
from bot.db.enums import PaymentProvider, PaymentStatus, TargetType
from bot.db.models import ActivePermission, ChatSettings, InboxSettings, PaymentOrder, Subscription
from bot.services.fulfillment import fulfill_order
from bot.services.payments.click import process_click_complete, process_click_prepare
from bot.services.payments.orders import create_payment_order
from tests.test_delivery import FakeBot


@pytest.mark.asyncio
async def test_fulfill_dm_session(session, make_user):
    buyer = await make_user()
    creator = await make_user()
    bot = FakeBot()

    order = await create_payment_order(
        session,
        user_id=buyer.id,
        recipient_id=creator.id,
        target_type=TargetType.DM_SESSION,
        target_id=creator.id,
        amount=15000,
        currency="UZS",
    )

    success = await fulfill_order(bot, session, order)
    assert success is True
    assert order.status == PaymentStatus.PAID
    assert order.completed_at is not None


@pytest.mark.asyncio
async def test_fulfill_channel_subscription(session, make_user):
    user = await make_user()
    owner = await make_user()
    bot = FakeBot()

    chat = ChatSettings(
        chat_id=-1001234567890,
        owner_id=owner.id,
        title="VIP Channel",
        chat_type="channel",
        enabled=True,
    )
    session.add(chat)
    await session.flush()

    order = await create_payment_order(
        session,
        user_id=user.id,
        recipient_id=owner.id,
        target_type=TargetType.CHANNEL_SUB,
        target_id=chat.chat_id,
        amount=50000,
        currency="UZS",
    )

    success = await fulfill_order(bot, session, order)
    assert success is True
    assert order.status == PaymentStatus.PAID
