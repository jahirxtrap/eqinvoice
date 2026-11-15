from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from eqinvoice import (
    AnyVoucher,
    Buyer,
    Environment,
    Issuer,
    Line,
    Message,
    Modified,
    Payment,
    PaymentForm,
    PeriodSales,
    Voucher,
    VoucherKind,
)


def unnumbered(kind: VoucherKind, on: date) -> dict[str, Any]:
    """What a draft holds in place of its numbering, which issuing takes from the profile."""
    return {
        "kind": kind,
        "environment": Environment.TEST,
        "access_key": "",
        "establishment": "",
        "point": "",
        "sequential": 0,
        "establishment_address": "",
        "issued_on": on,
    }


def paid(voucher: Voucher, form: PaymentForm | None) -> Voucher:
    return voucher if form is None else replace(voucher, payments=(Payment(form, voucher.total),))


class Standing(StrEnum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    REFUSED = "refused"


@dataclass(frozen=True)
class Lockup:
    """A brand's own name size and gap, as fractions of its mark; the library's when absent."""

    name_size: float | None = None
    gap: float | None = None


@dataclass(frozen=True)
class Profile:
    issuer: Issuer
    environment: Environment
    establishment: str
    point: str
    establishment_address: str


@dataclass(frozen=True)
class Draft:
    identifier: str
    voucher: AnyVoucher
    created_at: datetime

    @property
    def kind(self) -> VoucherKind:
        return self.voucher.kind


@dataclass(frozen=True)
class LegacyDraft:
    """A draft as 1.1 kept it, read so that upgrading loses none."""

    identifier: str
    kind: VoucherKind
    buyer: Buyer
    lines: tuple[Line, ...]
    payment_form: PaymentForm | None
    modified: Modified | None
    created_at: datetime

    def upgraded(self) -> Draft:
        template = Voucher(
            **unnumbered(self.kind, self.created_at.date()),
            buyer=self.buyer,
            lines=self.lines,
            modified=self.modified,
        )
        return Draft(self.identifier, paid(template, self.payment_form), self.created_at)


@dataclass(frozen=True)
class Previewed:
    draft: Draft
    voucher: AnyVoucher
    preview: Path


@dataclass(frozen=True)
class Filed:
    voucher: AnyVoucher
    standing: Standing
    signed: Path
    authorized: Path | None
    printed: Path | None
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class PendingVoucher:
    kind: VoucherKind
    number: str


@dataclass(frozen=True)
class SalesReport:
    sales: PeriodSales
    pending: tuple[PendingVoucher, ...]
