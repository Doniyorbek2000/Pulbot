"""Pul birliklari bilan ishlash.

Ichki hisob-kitob **mXTR** (milli-yulduzcha) da olib boriladi:
    1 XTR (yulduzcha) = 1000 mXTR

Sabab: komissiya (masalan 5%) va ulushlarni butun sonlarda, yaxlitlash
xatosisiz hisoblash uchun. Telegram'ga invoice yuborilganda esa butun
yulduzchaga yaxlitlanadi (Telegram fraksiyani qabul qilmaydi).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MXTR_PER_XTR = 1000

#: Qo'llab-quvvatlanadigan ko'rsatish valyutalari
CURRENCIES = ("XTR", "UZS", "USD")

CURRENCY_SYMBOL = {"XTR": "⭐", "UZS": "so'm", "USD": "$"}


def stars_to_mxtr(stars: int | float | Decimal) -> int:
    """Yulduzcha -> mXTR."""
    return int((Decimal(str(stars)) * MXTR_PER_XTR).quantize(Decimal("1"), ROUND_HALF_UP))


def mxtr_to_stars(mxtr: int) -> Decimal:
    """mXTR -> yulduzcha (Decimal, fraksiya bilan)."""
    return (Decimal(mxtr) / MXTR_PER_XTR).quantize(Decimal("0.001"))


def mxtr_to_invoice_stars(mxtr: int) -> int:
    """Invoice uchun butun yulduzcha (yuqoriga yaxlitlanadi, min 1)."""
    stars = -(-mxtr // MXTR_PER_XTR)  # ceil division
    return max(1, int(stars))


def apply_bps(amount_mxtr: int, bps: int) -> int:
    """Bazis punktdagi ulushni hisoblaydi (500 bps = 5%). Pastga yaxlitlanadi."""
    if bps <= 0:
        return 0
    return (amount_mxtr * bps) // 10_000


def split_commission(amount_mxtr: int, commission_bps: int) -> tuple[int, int]:
    """(qabul qiluvchiga tushadigan, platforma komissiyasi) juftligini qaytaradi."""
    fee = apply_bps(amount_mxtr, commission_bps)
    return amount_mxtr - fee, fee


def convert(amount_mxtr: int, currency: str, rate_uzs: float, rate_usd: float) -> Decimal:
    """mXTR ni tanlangan valyutaga o'giradi."""
    stars = Decimal(amount_mxtr) / MXTR_PER_XTR
    if currency == "UZS":
        return (stars * Decimal(str(rate_uzs))).quantize(Decimal("1"), ROUND_HALF_UP)
    if currency == "USD":
        return (stars * Decimal(str(rate_usd))).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return stars.quantize(Decimal("0.001"))


def from_currency(amount: Decimal | float | int, currency: str, rate_uzs: float, rate_usd: float) -> int:
    """Foydalanuvchi kiritgan summani (so'm/dollar/yulduzcha) mXTR ga o'giradi."""
    value = Decimal(str(amount))
    if currency == "UZS":
        stars = value / Decimal(str(rate_uzs or 1))
    elif currency == "USD":
        stars = value / Decimal(str(rate_usd or 1))
    else:
        stars = value
    return int((stars * MXTR_PER_XTR).quantize(Decimal("1"), ROUND_HALF_UP))


def _trim(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def group_digits(text: str) -> str:
    """1234567 -> 1 234 567 (kasr qismini saqlagan holda)."""
    negative = text.startswith("-")
    text = text.lstrip("-")
    whole, _, frac = text.partition(".")
    chunks = []
    while len(whole) > 3:
        chunks.insert(0, whole[-3:])
        whole = whole[:-3]
    chunks.insert(0, whole)
    out = " ".join(chunks)
    if frac:
        out = f"{out}.{frac}"
    return f"-{out}" if negative else out


def format_amount(
    amount_mxtr: int,
    currency: str = "XTR",
    *,
    rate_uzs: float = 0.0,
    rate_usd: float = 0.0,
    with_symbol: bool = True,
) -> str:
    """Summani foydalanuvchiga ko'rsatish uchun formatlaydi."""
    currency = currency if currency in CURRENCIES else "XTR"
    value = convert(amount_mxtr, currency, rate_uzs, rate_usd)
    text = group_digits(_trim(value))
    if not with_symbol:
        return text
    if currency == "XTR":
        return f"{text} ⭐"
    if currency == "UZS":
        return f"{text} so'm"
    return f"${text}"


def format_dual(
    amount_mxtr: int,
    currency: str,
    *,
    rate_uzs: float,
    rate_usd: float,
) -> str:
    """Asosiy valyuta + qavs ichida yulduzchadagi ekvivalent.

    Yulduzcha tanlangan bo'lsa takrorlamaydi.
    """
    primary = format_amount(amount_mxtr, currency, rate_uzs=rate_uzs, rate_usd=rate_usd)
    if currency == "XTR":
        return primary
    stars = format_amount(amount_mxtr, "XTR")
    return f"{primary} ({stars})"
