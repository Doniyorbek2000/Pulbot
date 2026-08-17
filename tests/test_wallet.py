"""Hamyon: kirim, chiqim, escrow, idempotentlik."""

import pytest

from bot.db.enums import TxKind
from bot.services import wallet
from bot.utils.money import stars_to_mxtr


async def test_credit_and_debit(session, make_user):
    user = await make_user()

    await wallet.credit(session, user.id, stars_to_mxtr(100), TxKind.TOPUP)
    balance, available = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(100) == available

    await wallet.debit(session, user.id, stars_to_mxtr(30), TxKind.MESSAGE_SPEND)
    balance, _ = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(70)


async def test_cannot_overdraw(session, make_user):
    user = await make_user(balance_mxtr=stars_to_mxtr(10))
    with pytest.raises(wallet.InsufficientFunds):
        await wallet.debit(session, user.id, stars_to_mxtr(11), TxKind.MESSAGE_SPEND)

    balance, _ = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(10)


async def test_locked_funds_are_not_spendable(session, make_user):
    user = await make_user(balance_mxtr=stars_to_mxtr(100))
    await wallet.lock(session, user.id, stars_to_mxtr(80))

    balance, available = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(100)
    assert available == stars_to_mxtr(20)

    with pytest.raises(wallet.InsufficientFunds):
        await wallet.debit(session, user.id, stars_to_mxtr(50), TxKind.MESSAGE_SPEND)

    # allow_locked bilan yechish mumkin (pul yechish yakunlanganda)
    await wallet.debit(
        session, user.id, stars_to_mxtr(50), TxKind.WITHDRAW_DONE, allow_locked=True
    )
    balance, _ = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(50)


async def test_idempotency_key_blocks_double_credit(session, make_user):
    user = await make_user()
    for _ in range(3):
        await wallet.credit(
            session, user.id, stars_to_mxtr(50), TxKind.TOPUP, idempotency_key="topup:abc"
        )
    balance, _ = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(50)


async def test_transfer_applies_commission(session, make_user):
    sender = await make_user(balance_mxtr=stars_to_mxtr(100))
    recipient = await make_user()

    result = await wallet.transfer(
        session,
        sender.id,
        recipient.id,
        stars_to_mxtr(100),
        500,  # 5%
        spend_kind=TxKind.CHAT_SPEND,
        earn_kind=TxKind.CHAT_EARN,
        ref_type="test",
        ref_id="1",
    )

    assert result.commission_mxtr == stars_to_mxtr(5)
    assert result.net_mxtr == stars_to_mxtr(95)

    sender_balance, _ = await wallet.balance(session, sender.id)
    recipient_balance, _ = await wallet.balance(session, recipient.id)
    assert sender_balance == 0
    assert recipient_balance == stars_to_mxtr(95)


async def test_escrow_hold_and_refund(session, make_user):
    sender = await make_user(balance_mxtr=stars_to_mxtr(50))
    recipient = await make_user()

    await wallet.hold(
        session, sender.id, stars_to_mxtr(50), ref_type="relay", ref_id=1,
        counterparty_id=recipient.id,
    )
    balance, _ = await wallet.balance(session, sender.id)
    assert balance == 0

    await wallet.refund(session, sender.id, stars_to_mxtr(50), ref_type="relay", ref_id=1)
    balance, _ = await wallet.balance(session, sender.id)
    assert balance == stars_to_mxtr(50)


async def test_history_is_recorded(session, make_user):
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(10), TxKind.TOPUP)
    await wallet.debit(session, user.id, stars_to_mxtr(4), TxKind.MESSAGE_SPEND)

    rows = await wallet.history(session, user.id)
    assert [row.kind for row in rows] == [TxKind.MESSAGE_SPEND, TxKind.TOPUP]
    assert rows[0].amount_mxtr == -stars_to_mxtr(4)
    assert rows[0].balance_after_mxtr == stars_to_mxtr(6)
