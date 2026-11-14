from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal("0")


class VoucherKind(StrEnum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"


class Environment(StrEnum):
    TEST = "test"
    PRODUCTION = "production"


class BuyerKind(StrEnum):
    RUC = "ruc"
    CEDULA = "cedula"
    PASSPORT = "passport"
    FINAL_CONSUMER = "final_consumer"


@dataclass(frozen=True)
class Issuer:
    legal_name: str
    ruc: str
    head_office_address: str
    keeps_accounts: bool
    trade_name: str | None = None
    special_taxpayer: str | None = None
    withholding_agent: str | None = None
    rimpe: bool = False
    system_provider_ruc: str | None = None


@dataclass(frozen=True)
class Buyer:
    """Whom the voucher is for; a final consumer carries neither identification nor name."""

    kind: BuyerKind
    identification: str | None = None
    name: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class Line:
    """One line, already split: every amount before IVA, then the IVA itself."""

    code: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    subtotal: Decimal
    vat: Decimal
    rate_code: str
    rate_percentage: Decimal


@dataclass(frozen=True)
class Payment:
    """Part of what an invoice was paid with: the SRI's payment form code and the amount."""

    form: str
    amount: Decimal


@dataclass(frozen=True)
class Modified:
    number: str
    issued_on: date
    reason: str


@dataclass(frozen=True)
class TaxGroup:
    code: str
    base: Decimal
    vat: Decimal
    rate: Decimal


@dataclass(frozen=True)
class Voucher:
    kind: VoucherKind
    environment: Environment
    access_key: str
    establishment: str
    point: str
    sequential: int
    establishment_address: str
    issued_on: date
    buyer: Buyer
    lines: tuple[Line, ...]
    payments: tuple[Payment, ...] = ()
    modified: Modified | None = None
    extra: tuple[tuple[str, str], ...] = ()

    @property
    def number(self) -> str:
        return f"{self.establishment}-{self.point}-{self.sequential:09d}"

    @property
    def subtotal(self) -> Decimal:
        return sum((line.subtotal for line in self.lines), ZERO)

    @property
    def discount(self) -> Decimal:
        return sum((line.discount for line in self.lines), ZERO)

    @property
    def vat(self) -> Decimal:
        return sum((line.vat for line in self.lines), ZERO)

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.vat

    @property
    def tax_groups(self) -> tuple[TaxGroup, ...]:
        """The lines added up per IVA rate, in the order the rates first appear."""
        groups: dict[str, TaxGroup] = {}
        for line in self.lines:
            held = groups.get(line.rate_code)
            base, vat = (held.base, held.vat) if held else (ZERO, ZERO)
            groups[line.rate_code] = TaxGroup(
                line.rate_code, base + line.subtotal, vat + line.vat, line.rate_percentage
            )
        return tuple(groups.values())

    @property
    def payment_forms(self) -> tuple[Payment, ...]:
        """The payments added up per form, in the order the forms first appear."""
        forms: dict[str, Decimal] = {}
        for payment in self.payments:
            forms[payment.form] = forms.get(payment.form, ZERO) + payment.amount
        return tuple(Payment(form, amount) for form, amount in forms.items())


@dataclass(frozen=True)
class Issued:
    voucher: Voucher
    issuer: Issuer
    authorized_at: datetime
    authorization_number: str
    total: Decimal
    ice: Decimal = ZERO
    irbpnr: Decimal = ZERO
    tip: Decimal | None = None
