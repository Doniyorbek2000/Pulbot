"""Pul birliklari va konvertatsiya testlari."""

from decimal import Decimal

import pytest

from bot.utils.money import (
    MXTR_PER_XTR,
    apply_bps,
    convert,
    format_amount,
    from_currency,
    group_digits,
    mxtr_to_invoice_stars,
    split_commission,
    stars_to_mxtr,
)


def test_stars_roundtrip():
    assert stars_to_mxtr(1) == MXTR_PER_XTR
    assert stars_to_mxtr(Decimal("2.5")) == 2500


def test_commission_never_loses_money():
    """Komissiya ayirilgach yig'indi asl summaga teng bo'lishi shart."""
    for amount in (1, 7, 999, 1000, 12345, 999_999):
        net, fee = split_commission(amount, 500)
        assert net + fee == amount
        assert fee >= 0 and net >= 0


def test_apply_bps_rounds_down():
    assert apply_bps(1000, 500) == 50
    assert apply_bps(99, 500) == 4  # 4.95 -> 4, foydalanuvchi foydasiga
    assert apply_bps(1000, 0) == 0


def test_invoice_stars_round_up():
    """Telegram butun yulduzcha qabul qiladi — pastga yaxlitlash zarar keltiradi."""
    assert mxtr_to_invoice_stars(1000) == 1
    assert mxtr_to_invoice_stars(1001) == 2
    assert mxtr_to_invoice_stars(1) == 1
    assert mxtr_to_invoice_stars(0) == 1


def test_currency_conversion():
    # 1 yulduzcha = 170 so'm
    assert convert(stars_to_mxtr(10), "UZS", 170, 0.013) == Decimal("1700")
    assert convert(stars_to_mxtr(100), "USD", 170, 0.013) == Decimal("1.30")


def test_from_currency_is_inverse():
    mxtr = from_currency(1700, "UZS", 170, 0.013)
    assert mxtr == stars_to_mxtr(10)
    assert from_currency(25, "XTR", 170, 0.013) == stars_to_mxtr(25)


def test_group_digits():
    assert group_digits("1234567") == "1 234 567"
    assert group_digits("999") == "999"
    assert group_digits("1234.5") == "1 234.5"


@pytest.mark.parametrize(
    ("currency", "expected"),
    [("XTR", "10 ⭐"), ("UZS", "1 700 so'm"), ("USD", "$0.13")],
)
def test_format_amount(currency, expected):
    assert format_amount(stars_to_mxtr(10), currency, rate_uzs=170, rate_usd=0.013) == expected
