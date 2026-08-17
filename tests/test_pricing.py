"""Narxlash dvigateli: rejimlar, istisnolar, jadval, limitlar."""

from datetime import datetime, timezone


from bot.db.enums import AccessRuleKind, ChatMode, InboxMode, ScheduleAction
from bot.db.models import ChatSchedule, ChatSettings, InboxSchedule
from bot.services import access, pricing, users
from bot.services.pricing import Reason
from bot.utils.money import stars_to_mxtr
from bot.utils.timeutils import ALL_DAYS, WEEKDAYS


async def _inbox(session, user, **kwargs):
    inbox = await users.get_inbox(session, user.id)
    for key, value in kwargs.items():
        setattr(inbox, key, value)
    await session.flush()
    return inbox


# --------------------------------------------------------------------------
# Rejimlar
# --------------------------------------------------------------------------


async def test_open_mode_is_free(session, make_user):
    owner = await make_user()
    sender = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.OPEN)

    quote = await pricing.quote_dm(session, sender, owner, inbox)
    assert quote.allowed and quote.is_free


async def test_paid_mode_charges(session, make_user):
    owner = await make_user()
    sender = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.PAID, price_mxtr=stars_to_mxtr(50))

    quote = await pricing.quote_dm(session, sender, owner, inbox)
    assert quote.needs_payment
    assert quote.price_mxtr == stars_to_mxtr(50)
    assert quote.reason == Reason.PRICE


async def test_premium_only_blocks_non_premium(session, make_user):
    owner = await make_user()
    regular = await make_user(is_premium=False)
    premium = await make_user(is_premium=True)
    inbox = await _inbox(session, owner, mode=InboxMode.PREMIUM_ONLY)

    assert not (await pricing.quote_dm(session, regular, owner, inbox)).allowed
    quote = await pricing.quote_dm(session, premium, owner, inbox)
    assert quote.allowed and quote.is_free and quote.reason == Reason.PREMIUM


async def test_premium_or_paid(session, make_user):
    """Eng ommabop rejim: Premium bepul, qolganlar to'laydi."""
    owner = await make_user()
    regular = await make_user(is_premium=False)
    premium = await make_user(is_premium=True)
    inbox = await _inbox(
        session, owner, mode=InboxMode.PREMIUM_OR_PAID, price_mxtr=stars_to_mxtr(20)
    )

    assert (await pricing.quote_dm(session, premium, owner, inbox)).is_free
    assert (await pricing.quote_dm(session, regular, owner, inbox)).price_mxtr == stars_to_mxtr(20)


async def test_closed_mode(session, make_user):
    owner = await make_user()
    sender = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.CLOSED)

    quote = await pricing.quote_dm(session, sender, owner, inbox)
    assert not quote.allowed and quote.reason == Reason.CLOSED


async def test_self_is_always_free(session, make_user):
    owner = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.CLOSED, price_mxtr=stars_to_mxtr(999))

    quote = await pricing.quote_dm(session, owner, owner, inbox)
    assert quote.is_free and quote.reason == Reason.SELF


# --------------------------------------------------------------------------
# Istisnolar
# --------------------------------------------------------------------------


async def test_whitelist_overrides_price(session, make_user):
    owner = await make_user()
    friend = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.PAID, price_mxtr=stars_to_mxtr(100))

    await access.set_rule(session, friend.id, AccessRuleKind.FREE, owner_id=owner.id)
    quote = await pricing.quote_dm(session, friend, owner, inbox)
    assert quote.is_free and quote.reason == Reason.RULE_FREE


async def test_blocklist_denies_even_in_open_mode(session, make_user):
    owner = await make_user()
    spammer = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.OPEN)

    await access.set_rule(session, spammer.id, AccessRuleKind.BLOCKED, owner_id=owner.id)
    quote = await pricing.quote_dm(session, spammer, owner, inbox)
    assert not quote.allowed and quote.reason == Reason.RULE_BLOCKED


async def test_custom_price_rule(session, make_user):
    owner = await make_user()
    vip = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.PAID, price_mxtr=stars_to_mxtr(100))

    await access.set_rule(
        session, vip.id, AccessRuleKind.CUSTOM_PRICE, owner_id=owner.id,
        price_mxtr=stars_to_mxtr(10),
    )
    quote = await pricing.quote_dm(session, vip, owner, inbox)
    assert quote.price_mxtr == stars_to_mxtr(10)


async def test_rules_are_per_owner(session, make_user):
    """Bir egadagi bloklash boshqasiga ta'sir qilmasligi kerak."""
    owner_a = await make_user()
    owner_b = await make_user()
    sender = await make_user()

    await access.set_rule(session, sender.id, AccessRuleKind.BLOCKED, owner_id=owner_a.id)

    inbox_b = await _inbox(session, owner_b, mode=InboxMode.OPEN)
    assert (await pricing.quote_dm(session, sender, owner_b, inbox_b)).allowed


# --------------------------------------------------------------------------
# Birinchi xabar va limitlar
# --------------------------------------------------------------------------


async def test_first_message_free(session, make_user):
    owner = await make_user()
    sender = await make_user()
    inbox = await _inbox(
        session, owner, mode=InboxMode.PAID, price_mxtr=stars_to_mxtr(50),
        free_first_message=True,
    )

    first = await pricing.quote_dm(session, sender, owner, inbox, had_previous=False)
    assert first.is_free and first.reason == Reason.FIRST_FREE

    second = await pricing.quote_dm(session, sender, owner, inbox, had_previous=True)
    assert second.price_mxtr == stars_to_mxtr(50)


async def test_daily_limit_blocks(session, make_user):
    owner = await make_user()
    sender = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.OPEN, daily_message_limit=2)

    for _ in range(2):
        await access.bump_inbox_usage(session, owner.id, sender.id, owner.tz_offset_minutes)

    quote = await pricing.quote_dm(session, sender, owner, inbox)
    assert not quote.allowed and quote.reason == Reason.LIMIT_REACHED


async def test_per_sender_limit(session, make_user):
    owner = await make_user()
    noisy = await make_user()
    quiet = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.OPEN, per_sender_daily_limit=1)

    await access.bump_inbox_usage(session, owner.id, noisy.id, owner.tz_offset_minutes)

    assert not (await pricing.quote_dm(session, noisy, owner, inbox)).allowed
    assert (await pricing.quote_dm(session, quiet, owner, inbox)).allowed


# --------------------------------------------------------------------------
# Vaqt jadvali
# --------------------------------------------------------------------------


async def test_schedule_night_surcharge(session, make_user):
    """22:00–08:00 oralig'ida narx boshqacha bo'ladi."""
    owner = await make_user(tz_offset_minutes=0)
    sender = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.PAID, price_mxtr=stars_to_mxtr(10))

    session.add(
        InboxSchedule(
            user_id=owner.id, days_mask=ALL_DAYS, start_min=22 * 60, end_min=8 * 60,
            action=ScheduleAction.PRICE, price_mxtr=stars_to_mxtr(30),
        )
    )
    await session.flush()

    night = datetime(2026, 6, 1, 23, 30, tzinfo=timezone.utc)
    day = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    assert (await pricing.quote_dm(session, sender, owner, inbox, moment=night)).price_mxtr == stars_to_mxtr(30)
    assert (await pricing.quote_dm(session, sender, owner, inbox, moment=day)).price_mxtr == stars_to_mxtr(10)


async def test_schedule_free_window(session, make_user):
    owner = await make_user(tz_offset_minutes=0)
    sender = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.PAID, price_mxtr=stars_to_mxtr(10))

    session.add(
        InboxSchedule(
            user_id=owner.id, days_mask=WEEKDAYS, start_min=9 * 60, end_min=18 * 60,
            action=ScheduleAction.FREE,
        )
    )
    await session.flush()

    # 2026-06-01 — dushanba
    workday = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    weekend = datetime(2026, 6, 6, 10, 0, tzinfo=timezone.utc)

    assert (await pricing.quote_dm(session, sender, owner, inbox, moment=workday)).is_free
    assert (await pricing.quote_dm(session, sender, owner, inbox, moment=weekend)).price_mxtr > 0


async def test_schedule_closed_window(session, make_user):
    owner = await make_user(tz_offset_minutes=0)
    sender = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.PAID, price_mxtr=stars_to_mxtr(10))

    session.add(
        InboxSchedule(
            user_id=owner.id, days_mask=ALL_DAYS, start_min=0, end_min=6 * 60,
            action=ScheduleAction.CLOSED,
        )
    )
    await session.flush()

    moment = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)
    quote = await pricing.quote_dm(session, sender, owner, inbox, moment=moment)
    assert not quote.allowed and quote.reason == Reason.SCHEDULE_CLOSED


async def test_schedule_respects_timezone(session, make_user):
    """UTC+5 da soat 23:00 — UTC da 18:00; jadval egasining vaqti bo'yicha."""
    owner = await make_user(tz_offset_minutes=300)
    sender = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.PAID, price_mxtr=stars_to_mxtr(10))

    session.add(
        InboxSchedule(
            user_id=owner.id, days_mask=ALL_DAYS, start_min=22 * 60, end_min=23 * 60 + 59,
            action=ScheduleAction.PRICE, price_mxtr=stars_to_mxtr(99),
        )
    )
    await session.flush()

    # UTC 18:00 = Toshkentda 23:00 -> jadval ishlaydi
    moment = datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)
    assert (await pricing.quote_dm(session, sender, owner, inbox, moment=moment)).price_mxtr == stars_to_mxtr(99)


async def test_schedule_priority(session, make_user):
    owner = await make_user(tz_offset_minutes=0)
    sender = await make_user()
    inbox = await _inbox(session, owner, mode=InboxMode.PAID, price_mxtr=stars_to_mxtr(10))

    session.add(
        InboxSchedule(
            user_id=owner.id, days_mask=ALL_DAYS, start_min=0, end_min=1440,
            action=ScheduleAction.PRICE, price_mxtr=stars_to_mxtr(50), priority=100,
        )
    )
    session.add(
        InboxSchedule(
            user_id=owner.id, days_mask=ALL_DAYS, start_min=0, end_min=1440,
            action=ScheduleAction.FREE, priority=1,
        )
    )
    await session.flush()

    quote = await pricing.quote_dm(session, sender, owner, inbox)
    assert quote.is_free  # priority=1 yutadi


# --------------------------------------------------------------------------
# Guruh narxlari
# --------------------------------------------------------------------------


async def _chat(session, owner, **kwargs):
    chat = ChatSettings(
        chat_id=-100123, title="Test", owner_id=owner.id, enabled=True,
        mode=ChatMode.PAID, price_mxtr=stars_to_mxtr(5), tz_offset_minutes=0, **kwargs
    )
    session.add(chat)
    await session.flush()
    return chat


async def test_chat_charges_members(session, make_user):
    owner = await make_user()
    member = await make_user()
    chat = await _chat(session, owner)

    quote = await pricing.quote_chat(session, member, chat)
    assert quote.price_mxtr == stars_to_mxtr(5)


async def test_chat_owner_and_admins_free(session, make_user):
    owner = await make_user()
    admin = await make_user()
    chat = await _chat(session, owner, free_for_admins=True)

    assert (await pricing.quote_chat(session, owner, chat)).is_free
    assert (await pricing.quote_chat(session, admin, chat, is_chat_admin=True)).is_free


async def test_chat_content_pricing(session, make_user):
    owner = await make_user()
    member = await make_user()
    chat = await _chat(session, owner)
    chat.price_by_content = {"photo": stars_to_mxtr(20), "link": stars_to_mxtr(50)}
    await session.flush()

    assert (await pricing.quote_chat(session, member, chat, content_kind="text")).price_mxtr == stars_to_mxtr(5)
    assert (await pricing.quote_chat(session, member, chat, content_kind="photo")).price_mxtr == stars_to_mxtr(20)
    assert (await pricing.quote_chat(session, member, chat, content_kind="link")).price_mxtr == stars_to_mxtr(50)


async def test_chat_daily_free_quota(session, make_user):
    owner = await make_user()
    member = await make_user()
    chat = await _chat(session, owner, free_daily_quota=2)

    quote = await pricing.quote_chat(session, member, chat)
    assert quote.is_free and quote.reason == Reason.DAILY_QUOTA

    usage = await access.get_chat_usage(session, chat.chat_id, member.id, 0)
    usage.free_used = 2
    await session.flush()

    assert (await pricing.quote_chat(session, member, chat)).price_mxtr == stars_to_mxtr(5)


async def test_chat_disabled_is_free(session, make_user):
    owner = await make_user()
    member = await make_user()
    chat = await _chat(session, owner)
    chat.enabled = False
    await session.flush()

    assert (await pricing.quote_chat(session, member, chat)).is_free


async def test_chat_schedule_overrides(session, make_user):
    owner = await make_user()
    member = await make_user()
    chat = await _chat(session, owner)

    session.add(
        ChatSchedule(
            chat_id=chat.chat_id, days_mask=ALL_DAYS, start_min=0, end_min=1440,
            action=ScheduleAction.FREE,
        )
    )
    await session.flush()

    assert (await pricing.quote_chat(session, member, chat)).is_free
