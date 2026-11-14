from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from eqinvoice.errors import VoucherInvalid
from eqinvoice.model import ZERO, Voucher, VoucherKind


@dataclass(frozen=True)
class RateSales:
    """One IVA rate in a period: the invoiced base, that base less credit notes, and its IVA."""

    code: str
    rate: Decimal
    gross: Decimal
    net: Decimal
    vat: Decimal


@dataclass(frozen=True)
class PeriodSales:
    since: date
    until: date
    invoices: int
    credit_notes: int
    rates: tuple[RateSales, ...]
    vat: Decimal


def issued_between(vouchers: Iterable[Voucher], since: date, until: date) -> tuple[Voucher, ...]:
    """The vouchers issued from one day to the other, both included."""
    if since > until:
        raise VoucherInvalid(f"The period starts on {since}, after it ends on {until}")
    return tuple(voucher for voucher in vouchers if since <= voucher.issued_on <= until)


def period_sales(vouchers: Iterable[Voucher], since: date, until: date) -> PeriodSales:
    """The vouchers of a period added up per IVA rate, with its credit notes offsetting them."""
    held = issued_between(vouchers, since, until)
    rates: dict[str, RateSales] = {}
    for voucher in held:
        credit = voucher.kind == VoucherKind.CREDIT_NOTE
        sign = -1 if credit else 1
        for group in voucher.tax_groups:
            known = rates.get(group.code) or RateSales(group.code, group.rate, ZERO, ZERO, ZERO)
            rates[group.code] = replace(
                known,
                gross=known.gross if credit else known.gross + group.base,
                net=known.net + sign * group.base,
                vat=known.vat + sign * group.vat,
            )
    kinds = [voucher.kind for voucher in held]
    return PeriodSales(
        since=since,
        until=until,
        invoices=kinds.count(VoucherKind.INVOICE),
        credit_notes=kinds.count(VoucherKind.CREDIT_NOTE),
        rates=tuple(rates.values()),
        vat=sum((rate.vat for rate in rates.values()), ZERO),
    )
