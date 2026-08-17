"""Pul yechish: xavfsizlik muddati, tekshiruvlar, holat o'tishlari."""

from datetime import timedelta

import pytest

from bot.db.enums import PaymentProvider, PaymentStatus, TxKind, WithdrawMethod, WithdrawStatus
from bot.db.models import Payment
from bot.services import app_settings, wallet, withdrawals
from bot.utils.money import stars_to_mxtr
from bot.utils.timeutils import utcnow


async def _paid_topup(session, user, stars: int, *, hours_ago: int = 0) -> Payment:
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.STARS,
        status=PaymentStatus.PAID,
        amount_mxtr=stars_to_mxtr(stars),
        stars=stars,
        payload=f"topup:{user.id}:{stars}:{hours_ago}",
        paid_at=utcnow() - timedelta(hours=hours_ago),
    )
    session.add(payment)
    await session.flush()
    return payment


# --------------------------------------------------------------------------
# Xavfsizlik muddati
# --------------------------------------------------------------------------


async def test_recent_topup_is_not_withdrawable(session, make_user):
    """Yangi to'ldirilgan pulni darhol yechib bo'lmaydi (kartani yuvish oldini oladi)."""
    user = await make_user(balance_mxtr=stars_to_mxtr(1000))
    await _paid_topup(session, user, 1000, hours_ago=1)

    free, held = await withdrawals.available_to_withdraw(session, user.id)
    assert free == 0
    assert held == stars_to_mxtr(1000)


async def test_old_topup_becomes_withdrawable(session, make_user):
    user = await make_user(balance_mxtr=stars_to_mxtr(1000))
    await _paid_topup(session, user, 1000, hours_ago=100)  # 72 soatdan ko'p

    free, held = await withdrawals.available_to_withdraw(session, user.id)
    assert free == stars_to_mxtr(1000)
    assert held == 0


async def test_earned_money_is_immediately_withdrawable(session, make_user):
    """Ishlab topilgan mablag'ga xavfsizlik muddati qo'llanmaydi."""
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    free, held = await withdrawals.available_to_withdraw(session, user.id)
    assert free == stars_to_mxtr(5000)
    assert held == 0


# --------------------------------------------------------------------------
# So'rov yaratish
# --------------------------------------------------------------------------


async def test_create_request_locks_funds(session, make_user):
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    request = await withdrawals.create_request(
        session, user,
        amount_mxtr=stars_to_mxtr(2000),
        method=WithdrawMethod.CARD_UZS,
        destination="8600123412341234",
        destination_name="TEST USER",
    )

    assert request.status == WithdrawStatus.PENDING
    assert request.fee_mxtr == stars_to_mxtr(40)  # 2%
    assert request.net_mxtr == stars_to_mxtr(1960)

    balance, available = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(5000)          # balans hali yechilmagan
    assert available == stars_to_mxtr(3000)        # lekin ushlangan


async def test_cannot_withdraw_below_minimum(session, make_user):
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    with pytest.raises(withdrawals.WithdrawError) as exc:
        await withdrawals.create_request(
            session, user, amount_mxtr=stars_to_mxtr(10),
            method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
        )
    assert exc.value.key == "withdraw.not_enough"


async def test_cannot_withdraw_more_than_available(session, make_user):
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(2000), TxKind.MESSAGE_EARN)

    with pytest.raises(withdrawals.WithdrawError):
        await withdrawals.create_request(
            session, user, amount_mxtr=stars_to_mxtr(3000),
            method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
        )


async def test_invalid_card_is_rejected(session, make_user):
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    with pytest.raises(withdrawals.WithdrawError):
        await withdrawals.create_request(
            session, user, amount_mxtr=stars_to_mxtr(2000),
            method=WithdrawMethod.CARD_UZS, destination="8600-12",
        )


@pytest.mark.parametrize(
    ("method", "value", "valid"),
    [
        (WithdrawMethod.CARD_UZS, "8600123412341234", True),
        (WithdrawMethod.CARD_UZS, "860012341234", False),
        (WithdrawMethod.PAYME, "+998901234567", True),
        (WithdrawMethod.USDT, "T" + "A" * 33, True),
        (WithdrawMethod.USDT, "0xabc", False),
        (WithdrawMethod.STARS_GIFT, "@username", True),
    ],
)
def test_destination_validation(method, value, valid):
    normalized = withdrawals.normalize_destination(method, value)
    assert withdrawals.validate_destination(method, normalized) is valid


# --------------------------------------------------------------------------
# Holat o'tishlari
# --------------------------------------------------------------------------


async def test_full_payout_flow(session, make_user):
    user = await make_user()
    admin = await make_user(is_admin=True)
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    request = await withdrawals.create_request(
        session, user, amount_mxtr=stars_to_mxtr(2000),
        method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
    )

    await withdrawals.approve(session, request, admin.id)
    assert request.status == WithdrawStatus.APPROVED

    await withdrawals.mark_paid(session, request, admin.id, "CHEK-123")
    assert request.status == WithdrawStatus.PAID
    assert request.external_ref == "CHEK-123"

    balance, available = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(3000)
    assert available == stars_to_mxtr(3000)  # ushlash bo'shatilgan

    row = await wallet.get_wallet(session, user.id)
    assert row.total_withdrawn_mxtr == stars_to_mxtr(2000)
    assert row.locked_mxtr == 0


async def test_rejection_returns_funds(session, make_user):
    user = await make_user()
    admin = await make_user(is_admin=True)
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    request = await withdrawals.create_request(
        session, user, amount_mxtr=stars_to_mxtr(2000),
        method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
    )
    await withdrawals.reject(session, request, admin.id, "Karta noto'g'ri")

    balance, available = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(5000) == available


async def test_user_cancel_returns_funds(session, make_user):
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    request = await withdrawals.create_request(
        session, user, amount_mxtr=stars_to_mxtr(2000),
        method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
    )
    await withdrawals.cancel_request(session, request)

    assert request.status == WithdrawStatus.CANCELED
    _balance, available = await wallet.balance(session, user.id)
    assert available == stars_to_mxtr(5000)


async def test_cannot_pay_twice(session, make_user):
    user = await make_user()
    admin = await make_user(is_admin=True)
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    request = await withdrawals.create_request(
        session, user, amount_mxtr=stars_to_mxtr(2000),
        method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
    )
    await withdrawals.mark_paid(session, request, admin.id)

    with pytest.raises(withdrawals.WithdrawError):
        await withdrawals.mark_paid(session, request, admin.id)

    balance, _ = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(3000)


async def test_two_requests_cannot_exceed_balance(session, make_user):
    """Ikkita so'rov birgalikda balansdan oshib ketmasligi kerak."""
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(3000), TxKind.MESSAGE_EARN)

    await withdrawals.create_request(
        session, user, amount_mxtr=stars_to_mxtr(2000),
        method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
    )
    with pytest.raises(withdrawals.WithdrawError):
        await withdrawals.create_request(
            session, user, amount_mxtr=stars_to_mxtr(2000),
            method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
        )


async def test_risk_flags_new_account(session, make_user):
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.ADMIN_CREDIT)

    report = await withdrawals.assess_risk(session, user, stars_to_mxtr(5000))
    assert report.score > 0
    assert "new_account" in report.flags
    assert "earned_less_than_requested" in report.flags


async def test_withdraw_disabled_blocks_requests(session, make_user):
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)
    await app_settings.set_value(session, "withdraw_enabled", False)

    with pytest.raises(withdrawals.WithdrawError) as exc:
        await withdrawals.create_request(
            session, user, amount_mxtr=stars_to_mxtr(2000),
            method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
        )
    assert exc.value.key == "withdraw.disabled"


async def test_one_click_payout_deducts_balance(session, make_user):
    """Admin bir bosishda tasdiqlaydi — chek raqami majburiy emas."""
    user = await make_user()
    admin = await make_user(is_admin=True)
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    request = await withdrawals.create_request(
        session, user, amount_mxtr=stars_to_mxtr(2000),
        method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
        destination_name="ALISHER VALIYEV",
    )

    # Karta va ism saqlangan — admin ularni ko'radi
    assert request.destination == "8600123412341234"
    assert request.destination_name == "ALISHER VALIYEV"
    assert request.payout_currency == "UZS"
    assert request.payout_amount  # so'mdagi summa hisoblangan

    # Chek raqamisiz tasdiqlash
    await withdrawals.mark_paid(session, request, admin.id, None)

    assert request.status == WithdrawStatus.PAID
    assert request.external_ref is None
    assert request.admin_id == admin.id

    balance, available = await wallet.balance(session, user.id)
    assert balance == stars_to_mxtr(3000)   # aynan yechilgan summaga kamaydi
    assert available == stars_to_mxtr(3000)


async def test_payout_amount_matches_card_currency(session, make_user):
    """Admin kartaga tashlaydigan summa so'mda to'g'ri hisoblanishi kerak."""
    user = await make_user()
    await wallet.credit(session, user.id, stars_to_mxtr(5000), TxKind.MESSAGE_EARN)

    request = await withdrawals.create_request(
        session, user, amount_mxtr=stars_to_mxtr(2000),
        method=WithdrawMethod.CARD_UZS, destination="8600123412341234",
    )

    # net = 2000 - 2% = 1960 yulduzcha; kurs 170 so'm -> 333 200 so'm
    assert request.net_mxtr == stars_to_mxtr(1960)
    assert request.payout_amount == "333200"
    assert request.payout_currency == "UZS"
