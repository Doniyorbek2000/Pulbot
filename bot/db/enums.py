"""Tizimda ishlatiladigan konstantalar (DB da matn sifatida saqlanadi)."""

from __future__ import annotations


class InboxMode:
    """Shaxsiy xabarlar (DM) rejimi."""

    OPEN = "open"                       # hamma bepul yozadi
    PAID = "paid"                       # hamma pul to'laydi
    PREMIUM_ONLY = "premium_only"       # faqat Telegram Premium egalari (bepul)
    PREMIUM_OR_PAID = "premium_or_paid" # Premium bepul, qolganlar pul to'laydi
    CLOSED = "closed"                   # hech kim yoza olmaydi

    ALL = (OPEN, PAID, PREMIUM_ONLY, PREMIUM_OR_PAID, CLOSED)


class ChatMode:
    """Guruh/kanal rejimi."""

    FREE = "free"
    PAID = "paid"
    PREMIUM_ONLY = "premium_only"
    PREMIUM_OR_PAID = "premium_or_paid"

    ALL = (FREE, PAID, PREMIUM_ONLY, PREMIUM_OR_PAID)


class PricingUnit:
    PER_MESSAGE = "per_message"   # har bir xabar uchun
    PER_SESSION = "per_session"   # N daqiqalik suhbat uchun bir marta

    ALL = (PER_MESSAGE, PER_SESSION)


class TxKind:
    """Tranzaksiya turlari."""

    TOPUP = "topup"                     # balansni to'ldirish
    TOPUP_REFUND = "topup_refund"       # to'ldirish qaytarildi
    MESSAGE_HOLD = "message_hold"       # pullik xabar uchun ushlab turildi
    MESSAGE_SPEND = "message_spend"     # xabar uchun yechildi
    MESSAGE_EARN = "message_earn"       # xabardan tushum
    MESSAGE_REFUND = "message_refund"   # xabar qaytarildi
    CHAT_SPEND = "chat_spend"           # guruhda yozganlik uchun
    CHAT_EARN = "chat_earn"             # guruh egasiga tushum
    COMMISSION = "commission"           # platforma komissiyasi
    WITHDRAW_HOLD = "withdraw_hold"     # yechish uchun ushlandi
    WITHDRAW_DONE = "withdraw_done"     # yechildi
    WITHDRAW_CANCEL = "withdraw_cancel" # yechish bekor qilindi
    REFERRAL_BONUS = "referral_bonus"
    ADMIN_CREDIT = "admin_credit"       # admin qo'lda qo'shdi
    ADMIN_DEBIT = "admin_debit"         # admin qo'lda yechdi
    TIP = "tip"                         # ixtiyoriy choychaqa


class RelayStatus:
    FREE = "free"           # bepul yetkazildi
    HELD = "held"           # pul escrow'da
    RELEASED = "released"   # pul qabul qiluvchiga o'tdi
    REFUNDED = "refunded"   # pul yuboruvchiga qaytdi
    REJECTED = "rejected"   # qabul qiluvchi rad etdi (pul qaytdi)

    ALL = (FREE, HELD, RELEASED, REFUNDED, REJECTED)


class PaymentStatus:
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentProvider:
    STARS = "stars"       # Telegram Stars (XTR) — hozir ishlaydi
    CLICK = "click"       # keyinchalik
    PAYME = "payme"       # keyinchalik
    UZUM = "uzum"         # keyinchalik
    CARD = "card"         # qo'lda karta orqali
    MANUAL = "manual"     # admin qo'shgan


class WithdrawStatus:
    PENDING = "pending"     # ko'rib chiqilmoqda
    APPROVED = "approved"   # tasdiqlandi, to'lov kutilmoqda
    PAID = "paid"           # to'landi
    REJECTED = "rejected"
    CANCELED = "canceled"   # foydalanuvchi bekor qildi

    OPEN_STATES = (PENDING, APPROVED)


class WithdrawMethod:
    CARD_UZS = "card_uzs"
    PAYME = "payme"
    CLICK = "click"
    USDT = "usdt"
    STARS_GIFT = "stars_gift"

    ALL = (CARD_UZS, PAYME, CLICK, USDT, STARS_GIFT)


class AccessRuleKind:
    FREE = "free"                 # bepul yozadi (istisno)
    BLOCKED = "blocked"           # bloklangan
    CUSTOM_PRICE = "custom_price" # alohida narx


class ScheduleAction:
    FREE = "free"
    PRICE = "price"
    CLOSED = "closed"

    ALL = (FREE, PRICE, CLOSED)


class ContentKind:
    """Guruhda kontent turi bo'yicha narxlash uchun."""

    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    VOICE = "voice"
    STICKER = "sticker"
    ANIMATION = "animation"
    DOCUMENT = "document"
    LINK = "link"
    FORWARD = "forward"
    OTHER = "other"

    ALL = (TEXT, PHOTO, VIDEO, VOICE, STICKER, ANIMATION, DOCUMENT, LINK, FORWARD, OTHER)
