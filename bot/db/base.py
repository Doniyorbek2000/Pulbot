"""SQLAlchemy bazaviy klass va umumiy ustunlar."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, MetaData
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


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class IntPK:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
