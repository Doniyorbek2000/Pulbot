"""Vaqt jadvallari bilan ishlash yordamchilari.

Jadval yozuvi quyidagicha saqlanadi:
    days_mask  — hafta kunlari bitmask'i (bit 0 = dushanba ... bit 6 = yakshanba)
    start_min  — kun boshidan boshlanish daqiqasi (0..1439)
    end_min    — tugash daqiqasi (0..1440). start > end bo'lsa yarim tundan oshadi.
Vaqt egasining mahalliy vaqtida (tz_offset_minutes) tekshiriladi.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

ALL_DAYS = 0b1111111
WEEKDAYS = 0b0011111
WEEKEND = 0b1100000

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def to_local(moment: datetime, tz_offset_minutes: int) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone(timedelta(minutes=tz_offset_minutes)))


def local_day_key(moment: datetime, tz_offset_minutes: int) -> str:
    """Kunlik limitlar uchun kalit: 'YYYY-MM-DD' mahalliy vaqtda."""
    return to_local(moment, tz_offset_minutes).strftime("%Y-%m-%d")


def parse_hhmm(text: str) -> int | None:
    """'09:30' -> 570. Noto'g'ri format bo'lsa None."""
    text = text.strip().replace(".", ":").replace(" ", "")
    if ":" not in text:
        if text.isdigit() and len(text) in (3, 4):
            text = f"{text[:-2]}:{text[-2:]}"
        else:
            return None
    hh, _, mm = text.partition(":")
    if not (hh.isdigit() and mm.isdigit()):
        return None
    h, m = int(hh), int(mm)
    if not (0 <= h <= 24 and 0 <= m < 60):
        return None
    minutes = h * 60 + m
    return minutes if minutes <= 1440 else None


def format_hhmm(minutes: int) -> str:
    minutes = minutes % 1441
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def days_mask_from_keys(keys: list[str]) -> int:
    mask = 0
    for key in keys:
        if key in DAY_KEYS:
            mask |= 1 << DAY_KEYS.index(key)
    return mask


def days_mask_to_keys(mask: int) -> list[str]:
    return [key for index, key in enumerate(DAY_KEYS) if mask & (1 << index)]


def matches(
    days_mask: int,
    start_min: int,
    end_min: int,
    moment: datetime,
    tz_offset_minutes: int = 0,
) -> bool:
    """Berilgan payt jadval oynasiga tushadimi?"""
    local = to_local(moment, tz_offset_minutes)
    minute_of_day = local.hour * 60 + local.minute

    def day_allowed(dt: datetime) -> bool:
        return bool(days_mask & (1 << dt.weekday()))

    if start_min <= end_min:
        return day_allowed(local) and start_min <= minute_of_day < end_min

    # Yarim tundan oshadigan oyna: 22:00 -> 06:00
    if day_allowed(local) and minute_of_day >= start_min:
        return True
    previous = local - timedelta(days=1)
    return day_allowed(previous) and minute_of_day < end_min


def humanize_timedelta(delta: timedelta, lang: str = "uz") -> str:
    total = max(0, int(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    units = {
        "uz": ("kun", "soat", "daqiqa"),
        "ru": ("д.", "ч.", "мин."),
        "en": ("d", "h", "m"),
    }.get(lang, ("kun", "soat", "daqiqa"))
    parts: list[str] = []
    if days:
        parts.append(f"{days} {units[0]}")
    if hours:
        parts.append(f"{hours} {units[1]}")
    if minutes and not days:
        parts.append(f"{minutes} {units[2]}")
    return " ".join(parts) or f"< 1 {units[2]}"
