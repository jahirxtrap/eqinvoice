from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import TypeVar

from eqinvoice.errors import VoucherInvalid
from eqinvoice.model import ZERO, Numbered, Voucher, VoucherKind

Dated = TypeVar("Dated", bound=Numbered)
SALE_SIGNS = {VoucherKind.INVOICE: 1, VoucherKind.DEBIT_NOTE: 1, VoucherKind.CREDIT_NOTE: -1}


@dataclass(frozen=True)
class RateSales:
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
    debit_notes: int = 0


def issued_between(vouchers: Iterable[Dated], since: date, until: date) -> tuple[Dated, ...]:
    """The vouchers issued from one day to the other, both included."""
    if since > until:
        raise VoucherInvalid(f"The period starts on {since}, after it ends on {until}")
    return tuple(voucher for voucher in vouchers if since <= voucher.issued_on <= until)


def period_sales(vouchers: Iterable[Numbered], since: date, until: date) -> PeriodSales:
    """Invoices and debit notes add up, credit notes offset them; other kinds are no sales."""
    held = [
        voucher
        for voucher in issued_between(vouchers, since, until)
        if isinstance(voucher, Voucher) and voucher.kind in SALE_SIGNS
    ]
    rates: dict[str, RateSales] = {}
    for voucher in held:
        sign = SALE_SIGNS[voucher.kind]
        for group in voucher.tax_groups:
            known = rates.get(group.code) or RateSales(group.code, group.rate, ZERO, ZERO, ZERO)
            rates[group.code] = replace(
                known,
                gross=known.gross + max(sign, 0) * group.base,
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
        debit_notes=kinds.count(VoucherKind.DEBIT_NOTE),
    )
