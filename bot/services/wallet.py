"""Hamyon xizmati — barcha pul harakatlari shu yerdan o'tadi.

Qoidalar:
  * Balans hech qachon manfiy bo'lmaydi.
  * Har bir harakat `transactions` jadvaliga yoziladi (audit).
  * `idempotency_key` bir xil amal ikki marta bajarilishining oldini oladi.
  * Escrow uchun `locked_mxtr` ishlatiladi: pul balansda turadi, lekin
    sarflab bo'lmaydi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import TxKind
from bot.db.models import Transaction, Wallet
from bot.utils.money import split_commission

logger = logging.getLogger(__name__)


class WalletError(Exception):
    """Hamyon bilan bog'liq umumiy xato."""


class InsufficientFunds(WalletError):
    def __init__(self, needed_mxtr: int, available_mxtr: int) -> None:
        self.needed_mxtr = needed_mxtr
        self.available_mxtr = available_mxtr
        super().__init__(f"Mablag' yetarli emas: kerak {needed_mxtr}, mavjud {available_mxtr}")


@dataclass(slots=True)
class TransferResult:
    gross_mxtr: int
    net_mxtr: int
    commission_mxtr: int


async def get_wallet(session: AsyncSession, user_id: int, *, for_update: bool = False) -> Wallet:
    """Hamyonni oladi, bo'lmasa yaratadi."""
    stmt = select(Wallet).where(Wallet.user_id == user_id)
    if for_update and session.bind is not None and session.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    wallet = (await session.execute(stmt)).scalar_one_or_none()
    if wallet is None:
        wallet = Wallet(user_id=user_id)
        session.add(wallet)
        try:
            await session.flush()
        except IntegrityError:  # boshqa oqim allaqachon yaratdi
            await session.rollback()
            wallet = (await session.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one()
    return wallet


async def balance(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """(umumiy balans, sarflash mumkin bo'lgan) mXTR."""
    wallet = await get_wallet(session, user_id)
    return wallet.balance_mxtr, wallet.available_mxtr


async def _existing(session: AsyncSession, key: str | None) -> Transaction | None:
    if not key:
        return None
    return (
        await session.execute(select(Transaction).where(Transaction.idempotency_key == key))
    ).scalar_one_or_none()


async def _record(
    session: AsyncSession,
    *,
    wallet: Wallet,
    kind: str,
    amount_mxtr: int,
    counterparty_id: int | None = None,
    chat_id: int | None = None,
    ref_type: str | None = None,
    ref_id: str | int | None = None,
    note: str | None = None,
    extra: dict | None = None,
    idempotency_key: str | None = None,
) -> Transaction:
    tx = Transaction(
        user_id=wallet.user_id,
        kind=kind,
        amount_mxtr=amount_mxtr,
        balance_after_mxtr=wallet.balance_mxtr,
        counterparty_id=counterparty_id,
        chat_id=chat_id,
        ref_type=ref_type,
        ref_id=str(ref_id) if ref_id is not None else None,
        note=note,
        extra=extra,
        idempotency_key=idempotency_key,
    )
    session.add(tx)
    await session.flush()
    return tx


async def credit(
    session: AsyncSession,
    user_id: int,
    amount_mxtr: int,
    kind: str,
    **kwargs,
) -> Transaction:
    """Balansga pul qo'shish."""
    if amount_mxtr <= 0:
        raise WalletError("Summa musbat bo'lishi kerak")

    key = kwargs.get("idempotency_key")
    if (done := await _existing(session, key)) is not None:
        logger.info("Idempotent kirim o'tkazib yuborildi: %s", key)
        return done

    wallet = await get_wallet(session, user_id, for_update=True)
    wallet.balance_mxtr += amount_mxtr

    if kind in (TxKind.TOPUP, TxKind.ADMIN_CREDIT):
        wallet.total_topup_mxtr += amount_mxtr
    elif kind in (TxKind.MESSAGE_EARN, TxKind.CHAT_EARN, TxKind.TIP, TxKind.REFERRAL_BONUS):
        wallet.total_earned_mxtr += amount_mxtr

    return await _record(session, wallet=wallet, kind=kind, amount_mxtr=amount_mxtr, **kwargs)


async def debit(
    session: AsyncSession,
    user_id: int,
    amount_mxtr: int,
    kind: str,
    *,
    allow_locked: bool = False,
    **kwargs,
) -> Transaction:
    """Balansdan pul yechish.

    `allow_locked=True` bo'lsa ushlab turilgan (locked) mablag'dan ham
    yechishga ruxsat beriladi — escrow yopilayotganda ishlatiladi.
    """
    if amount_mxtr <= 0:
        raise WalletError("Summa musbat bo'lishi kerak")

    key = kwargs.get("idempotency_key")
    if (done := await _existing(session, key)) is not None:
        return done

    wallet = await get_wallet(session, user_id, for_update=True)
    usable = wallet.balance_mxtr if allow_locked else wallet.available_mxtr
    if usable < amount_mxtr:
        raise InsufficientFunds(amount_mxtr, usable)

    wallet.balance_mxtr -= amount_mxtr
    if kind in (TxKind.MESSAGE_SPEND, TxKind.CHAT_SPEND):
        wallet.total_spent_mxtr += amount_mxtr
    elif kind == TxKind.WITHDRAW_DONE:
        wallet.total_withdrawn_mxtr += amount_mxtr

    return await _record(session, wallet=wallet, kind=kind, amount_mxtr=-amount_mxtr, **kwargs)


async def lock(session: AsyncSession, user_id: int, amount_mxtr: int) -> None:
    """Mablag'ni ushlab turish (escrow / pul yechish so'rovi)."""
    wallet = await get_wallet(session, user_id, for_update=True)
    if wallet.available_mxtr < amount_mxtr:
        raise InsufficientFunds(amount_mxtr, wallet.available_mxtr)
    wallet.locked_mxtr += amount_mxtr
    await session.flush()


async def unlock(session: AsyncSession, user_id: int, amount_mxtr: int) -> None:
    """Ushlab turilgan mablag'ni bo'shatish."""
    wallet = await get_wallet(session, user_id, for_update=True)
    wallet.locked_mxtr = max(0, wallet.locked_mxtr - amount_mxtr)
    await session.flush()


async def hold(
    session: AsyncSession,
    user_id: int,
    amount_mxtr: int,
    *,
    ref_type: str,
    ref_id: str | int,
    counterparty_id: int | None = None,
) -> Transaction:
    """Pullik xabar uchun mablag'ni yechib, escrow'ga o'tkazish.

    Pul yuboruvchidan darrov yechiladi (u boshqa joyga sarflay olmasligi
    uchun), lekin qabul qiluvchiga hali o'tmaydi.
    """
    return await debit(
        session,
        user_id,
        amount_mxtr,
        TxKind.MESSAGE_HOLD,
        ref_type=ref_type,
        ref_id=ref_id,
        counterparty_id=counterparty_id,
        idempotency_key=f"hold:{ref_type}:{ref_id}",
        note="Escrow",
    )


async def release_to(
    session: AsyncSession,
    recipient_id: int,
    amount_mxtr: int,
    commission_bps: int,
    *,
    kind: str = TxKind.MESSAGE_EARN,
    ref_type: str,
    ref_id: str | int,
    counterparty_id: int | None = None,
    chat_id: int | None = None,
) -> TransferResult:
    """Escrow'dagi mablag'ni komissiyani ayirib qabul qiluvchiga o'tkazish."""
    net, fee = split_commission(amount_mxtr, commission_bps)
    if net > 0:
        await credit(
            session,
            recipient_id,
            net,
            kind,
            ref_type=ref_type,
            ref_id=ref_id,
            counterparty_id=counterparty_id,
            chat_id=chat_id,
            idempotency_key=f"earn:{ref_type}:{ref_id}",
            note=f"Komissiya: {fee}",
            extra={"gross_mxtr": amount_mxtr, "commission_mxtr": fee},
        )
    return TransferResult(gross_mxtr=amount_mxtr, net_mxtr=net, commission_mxtr=fee)


async def refund(
    session: AsyncSession,
    user_id: int,
    amount_mxtr: int,
    *,
    ref_type: str,
    ref_id: str | int,
    kind: str = TxKind.MESSAGE_REFUND,
    note: str | None = None,
) -> Transaction:
    """Escrow'dagi pulni yuboruvchiga qaytarish."""
    return await credit(
        session,
        user_id,
        amount_mxtr,
        kind,
        ref_type=ref_type,
        ref_id=ref_id,
        idempotency_key=f"refund:{ref_type}:{ref_id}",
        note=note or "Qaytarildi",
    )


async def transfer(
    session: AsyncSession,
    sender_id: int,
    recipient_id: int,
    amount_mxtr: int,
    commission_bps: int,
    *,
    spend_kind: str,
    earn_kind: str,
    ref_type: str,
    ref_id: str | int,
    chat_id: int | None = None,
) -> TransferResult:
    """Escrow'siz to'g'ridan-to'g'ri o'tkazma (guruhdagi xabarlar uchun)."""
    await debit(
        session,
        sender_id,
        amount_mxtr,
        spend_kind,
        ref_type=ref_type,
        ref_id=ref_id,
        counterparty_id=recipient_id,
        chat_id=chat_id,
        idempotency_key=f"spend:{ref_type}:{ref_id}",
    )
    return await release_to(
        session,
        recipient_id,
        amount_mxtr,
        commission_bps,
        kind=earn_kind,
        ref_type=ref_type,
        ref_id=ref_id,
        counterparty_id=sender_id,
        chat_id=chat_id,
    )


async def history(
    session: AsyncSession,
    user_id: int,
    *,
    limit: int = 10,
    offset: int = 0,
    kinds: tuple[str, ...] | None = None,
) -> list[Transaction]:
    stmt = select(Transaction).where(Transaction.user_id == user_id)
    if kinds:
        stmt = stmt.where(Transaction.kind.in_(kinds))
    stmt = stmt.order_by(Transaction.id.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())
