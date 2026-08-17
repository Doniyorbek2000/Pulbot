"""SQLAlchemy bazaviy klass va umumiy ustunlar."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, MetaData, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Avtoinkrement qilinadigan birlamchi kalit uchun tur.
#: SQLite faqat `INTEGER PRIMARY KEY` ustunini avtomatik to'ldiradi — sof
#: BIGINT bo'lsa AUTOINCREMENT ishlamaydi va INSERT xato beradi. PostgreSQL
#: da esa BIGINT (bigserial) kerak, shuning uchun variant ishlatiladi.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime(timezone=True) SQLite'da tzinfo'ni saqlamaydi — o'qishda
    naive datetime qaytaradi. Bu esa `utcnow() - db_dan_o'qilgan_vaqt` kabi
    hisoblarda "can't subtract offset-naive and offset-aware datetimes"
    xatosiga olib keladi. Shu type har doim UTC tzinfo bilan qaytarishni
    kafolatlaydi (PostgreSQL'da ham xavfsiz — u tzinfo'ni saqlaydi)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class IntPK:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
