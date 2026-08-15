"""Ma'lumotlar bazasi modellari."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.config import settings
from bot.db.base import Base, BigIntPK, TimestampMixin, utcnow
from bot.db.enums import (
    ChatMode,
    InboxMode,
    PaymentStatus,
    PricingUnit,
    RelayStatus,
    WithdrawStatus,
)

# ---------------------------------------------------------------------------
# Foydalanuvchi va hamyon
# ---------------------------------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str | None] = mapped_column(String(128))

    language: Mapped[str] = mapped_column(String(4), default=lambda: settings.default_language)
    display_currency: Mapped[str] = mapped_column(String(4), default="UZS")
    tz_offset_minutes: Mapped[int] = mapped_column(Integer, default=300)  # UTC+5 (Toshkent)

    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_reason: Mapped[str | None] = mapped_column(String(256))
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Deep-link uchun qisqa kod: t.me/bot?start=u_<public_code>
    public_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)

    referrer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    wallet: Mapped["Wallet"] = relationship(back_populates="user", uselist=False, lazy="selectin")
    inbox: Mapped["InboxSettings"] = relationship(back_populates="user", uselist=False, lazy="selectin")

    @property
    def full_name(self) -> str:
        return " ".join(filter(None, (self.first_name, self.last_name))) or f"#{self.id}"

    @property
    def mention(self) -> str:
        return f"@{self.username}" if self.username else self.full_name


class Wallet(Base, TimestampMixin):
    """Foydalanuvchi hamyoni. Barcha summalar mXTR (1 yulduzcha = 1000 mXTR)."""

    __tablename__ = "wallets"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    balance_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    #: Escrow yoki pul yechishga ushlab turilgan mablag'
    locked_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)

    total_topup_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    total_spent_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    total_earned_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    total_withdrawn_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)

    user: Mapped[User] = relationship(back_populates="wallet")

    @property
    def available_mxtr(self) -> int:
        return max(0, self.balance_mxtr - self.locked_mxtr)


class Transaction(Base):
    """Har bir pul harakati (audit uchun o'chirilmaydi)."""

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    #: Musbat — kirim, manfiy — chiqim
    amount_mxtr: Mapped[int] = mapped_column(BigInteger)
    balance_after_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)

    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[str | None] = mapped_column(String(64), index=True)
    counterparty_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, index=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    note: Mapped[str | None] = mapped_column(String(256))
    extra: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ---------------------------------------------------------------------------
# Shaxsiy xabarlar (DM) sozlamalari
# ---------------------------------------------------------------------------


class InboxSettings(Base, TimestampMixin):
    __tablename__ = "inbox_settings"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(24), default=InboxMode.OPEN)

    price_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    #: Narx qaysi valyutada belgilangan (ko'rsatish uchun)
    price_currency: Mapped[str] = mapped_column(String(4), default="UZS")

    pricing_unit: Mapped[str] = mapped_column(String(16), default=PricingUnit.PER_MESSAGE)
    session_minutes: Mapped[int] = mapped_column(Integer, default=60)

    #: Birinchi xabar bepul (tanishuv uchun)
    free_first_message: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Telegram Premium egalari bepul yozadi
    free_for_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Do'stlar (whitelist) avtomatik bepul — istisnolar jadvalidan
    free_for_referrals: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Escrow: javob berilmasa pul necha soatdan keyin egasiga o'tadi (0 = darrov)
    hold_hours: Mapped[int] = mapped_column(Integer, default=lambda: settings.default_hold_hours)
    #: Javob berilmasa avtomatik qaytarish (hold_hours dan keyin pul yuboruvchiga qaytadi)
    refund_if_no_reply: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Kuniga qabul qilinadigan maksimal xabar (0 = cheksiz)
    daily_message_limit: Mapped[int] = mapped_column(Integer, default=0)
    #: Bitta odamdan kuniga maksimal xabar (0 = cheksiz)
    per_sender_daily_limit: Mapped[int] = mapped_column(Integer, default=0)

    welcome_text: Mapped[str | None] = mapped_column(Text)
    #: Yozuvchiga profil ko'rsatilsinmi
    show_stats: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="inbox")


class InboxSchedule(Base, TimestampMixin):
    """Vaqtga bog'liq narx qoidalari (masalan: 22:00–08:00 — 2x narx)."""

    __tablename__ = "inbox_schedules"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)

    days_mask: Mapped[int] = mapped_column(Integer, default=0b1111111)
    start_min: Mapped[int] = mapped_column(Integer, default=0)
    end_min: Mapped[int] = mapped_column(Integer, default=1440)

    action: Mapped[str] = mapped_column(String(16), default="price")
    price_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    #: Kichik raqam = yuqori ustuvorlik
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str | None] = mapped_column(String(64))


class AccessRule(Base, TimestampMixin):
    """Istisnolar: bepul yozadiganlar, bloklanganlar, alohida narxlar.

    owner_id — qoida egasi (DM uchun), chat_id — guruh uchun (owner_id = 0).
    """

    __tablename__ = "access_rules"
    __table_args__ = (
        UniqueConstraint("owner_id", "chat_id", "target_id", name="uq_access_rule_scope"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    target_id: Mapped[int] = mapped_column(BigInteger, index=True)

    kind: Mapped[str] = mapped_column(String(16))
    price_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    note: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Xabar yetkazish (relay)
# ---------------------------------------------------------------------------


class RelaySession(Base):
    """Sessiya rejimida to'langan suhbat oynasi."""

    __tablename__ = "relay_sessions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    sender_id: Mapped[int] = mapped_column(BigInteger, index=True)
    recipient_id: Mapped[int] = mapped_column(BigInteger, index=True)
    paid_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    messages_sent: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RelayMessage(Base):
    """Bot orqali yetkazilgan har bir xabar."""

    __tablename__ = "relay_messages"
    __table_args__ = (
        Index("ix_relay_recipient_status", "recipient_id", "status"),
        Index("ix_relay_hold_until", "status", "hold_until"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    sender_id: Mapped[int] = mapped_column(BigInteger, index=True)
    recipient_id: Mapped[int] = mapped_column(BigInteger, index=True)
    session_id: Mapped[int | None] = mapped_column(BigInteger)

    price_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    net_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)      # komissiyadan keyin
    commission_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default=RelayStatus.FREE, index=True)

    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    delivered_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    #: Javob ketma-ketligini kuzatish uchun
    reply_to_relay_id: Mapped[int | None] = mapped_column(BigInteger)
    is_reply_from_owner: Mapped[bool] = mapped_column(Boolean, default=False)

    preview: Mapped[str | None] = mapped_column(String(160))
    content_kind: Mapped[str] = mapped_column(String(16), default="text")

    hold_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ---------------------------------------------------------------------------
# Guruh / kanal
# ---------------------------------------------------------------------------


class ChatSettings(Base, TimestampMixin):
    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    chat_type: Mapped[str] = mapped_column(String(16), default="group")
    title: Mapped[str] = mapped_column(String(256), default="")
    username: Mapped[str | None] = mapped_column(String(64))

    #: Tushum kimning hamyoniga tushadi
    owner_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    language: Mapped[str] = mapped_column(String(4), default=lambda: settings.default_language)
    tz_offset_minutes: Mapped[int] = mapped_column(Integer, default=300)

    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(24), default=ChatMode.FREE)

    price_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    price_currency: Mapped[str] = mapped_column(String(4), default="UZS")
    #: Kontent turi bo'yicha narx: {"photo": 2000, "link": 5000}
    price_by_content: Mapped[dict | None] = mapped_column(JSON)

    free_for_admins: Mapped[bool] = mapped_column(Boolean, default=True)
    free_for_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Kuniga har bir a'zoga necha bepul xabar
    free_daily_quota: Mapped[int] = mapped_column(Integer, default=0)
    #: Yangi a'zoga birinchi N xabar bepul
    free_first_messages: Mapped[int] = mapped_column(Integer, default=0)

    #: To'lay olmagan a'zoning xabari o'chirilsinmi
    delete_unpaid: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Ogohlantirish yuborilsinmi (bir necha soniyadan keyin o'chadi)
    warn_unpaid: Mapped[bool] = mapped_column(Boolean, default=True)
    warn_ttl_seconds: Mapped[int] = mapped_column(Integer, default=15)

    #: Tushumning necha foizi guruh egasiga (qolgani platformaga qo'shimcha)
    owner_share_bps: Mapped[int] = mapped_column(Integer, default=10_000)

    total_earned_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    total_messages_paid: Mapped[int] = mapped_column(Integer, default=0)
    bot_can_delete: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatSchedule(Base, TimestampMixin):
    __tablename__ = "chat_schedules"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chat_settings.chat_id", ondelete="CASCADE"), index=True)

    days_mask: Mapped[int] = mapped_column(Integer, default=0b1111111)
    start_min: Mapped[int] = mapped_column(Integer, default=0)
    end_min: Mapped[int] = mapped_column(Integer, default=1440)
    action: Mapped[str] = mapped_column(String(16), default="price")
    price_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str | None] = mapped_column(String(64))


class ChatUsage(Base):
    """Guruhda kunlik bepul limitlarni hisoblash."""

    __tablename__ = "chat_usage"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", "day", name="uq_chat_usage_day"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    day: Mapped[str] = mapped_column(String(10))
    free_used: Mapped[int] = mapped_column(Integer, default=0)
    paid_count: Mapped[int] = mapped_column(Integer, default=0)
    spent_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)


class InboxUsage(Base):
    """DM uchun kunlik limitlar."""

    __tablename__ = "inbox_usage"
    __table_args__ = (
        UniqueConstraint("owner_id", "sender_id", "day", name="uq_inbox_usage_day"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    #: 0 — umumiy kunlik hisob, aks holda aniq yuboruvchi
    sender_id: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    day: Mapped[str] = mapped_column(String(10))
    count: Mapped[int] = mapped_column(Integer, default=0)
    earned_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)


# ---------------------------------------------------------------------------
# To'lovlar va pul yechish
# ---------------------------------------------------------------------------


class Payment(Base, TimestampMixin):
    """Balansni to'ldirish yozuvi."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default=PaymentStatus.PENDING, index=True)

    amount_mxtr: Mapped[int] = mapped_column(BigInteger)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    #: Foydalanuvchi ko'rgan valyuta va summa (so'm/dollar)
    display_currency: Mapped[str] = mapped_column(String(4), default="XTR")
    display_amount: Mapped[str | None] = mapped_column(String(32))

    payload: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    telegram_charge_id: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_charge_id: Mapped[str | None] = mapped_column(String(128))
    external_ref: Mapped[str | None] = mapped_column(String(128))

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSON)


class Withdrawal(Base, TimestampMixin):
    """Pul yechish so'rovi."""

    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=WithdrawStatus.PENDING, index=True)

    amount_mxtr: Mapped[int] = mapped_column(BigInteger)     # yechilayotgan summa
    fee_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)
    net_mxtr: Mapped[int] = mapped_column(BigInteger, default=0)  # qo'lga tegadigan

    method: Mapped[str] = mapped_column(String(16))
    destination: Mapped[str] = mapped_column(String(128))     # karta raqami / hamyon
    destination_name: Mapped[str | None] = mapped_column(String(128))
    payout_currency: Mapped[str] = mapped_column(String(4), default="UZS")
    payout_amount: Mapped[str | None] = mapped_column(String(32))

    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_note: Mapped[str | None] = mapped_column(String(256))
    external_ref: Mapped[str | None] = mapped_column(String(128))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_flags: Mapped[dict | None] = mapped_column(JSON)


# ---------------------------------------------------------------------------
# Xizmat jadvallari
# ---------------------------------------------------------------------------


class AppSetting(Base, TimestampMixin):
    """Ish vaqtida o'zgartiriladigan global sozlamalar (kurslar, komissiya...)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BroadcastJob(Base, TimestampMixin):
    __tablename__ = "broadcast_jobs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(4))
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
