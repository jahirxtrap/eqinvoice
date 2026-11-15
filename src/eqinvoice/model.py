from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal("0")


class VoucherKind(StrEnum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"
    PURCHASE_SETTLEMENT = "purchase_settlement"
    REMISSION_GUIDE = "remission_guide"
    WITHHOLDING = "withholding"


class Environment(StrEnum):
    TEST = "test"
    PRODUCTION = "production"


class BuyerKind(StrEnum):
    RUC = "ruc"
    CEDULA = "cedula"
    PASSPORT = "passport"
    FINAL_CONSUMER = "final_consumer"
    FOREIGN = "foreign"


class RetentionTax(StrEnum):
    INCOME = "1"
    VAT = "2"
    ISD = "6"


class SubjectType(StrEnum):
    PERSON = "01"
    COMPANY = "02"


class PaymentForm(StrEnum):
    WITHOUT_FINANCIAL_SYSTEM = "01"
    DEBT_COMPENSATION = "15"
    DEBIT_CARD = "16"
    ELECTRONIC_MONEY = "17"
    PREPAID_CARD = "18"
    CREDIT_CARD = "19"
    WITH_FINANCIAL_SYSTEM = "20"
    ENDORSED_SECURITIES = "21"


def sequential_code(sequential: int) -> str:
    return f"{sequential:09d}"


def voucher_number(establishment: str, point: str, sequential: int) -> str:
    return f"{establishment}-{point}-{sequential_code(sequential)}"


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
    popular_business: bool = False


@dataclass(frozen=True)
class Buyer:
    """The other party: the buyer, a settlement's supplier, a guide's carrier or the withheld."""

    kind: BuyerKind
    identification: str | None = None
    name: str | None = None
    email: str | None = None
    address: str | None = None


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
    """Part of what an invoice was paid with: the SRI's payment form and the amount."""

    form: PaymentForm
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


def added_up(payments: tuple[Payment, ...]) -> tuple[Payment, ...]:
    """The payments added up per form, in the order the forms first appear."""
    forms: dict[PaymentForm, Decimal] = {}
    for payment in payments:
        forms[payment.form] = forms.get(payment.form, ZERO) + payment.amount
    return tuple(Payment(form, amount) for form, amount in forms.items())


@dataclass(frozen=True)
class Numbered:
    kind: VoucherKind
    environment: Environment
    access_key: str
    establishment: str
    point: str
    sequential: int
    establishment_address: str
    issued_on: date

    @property
    def number(self) -> str:
        return voucher_number(self.establishment, self.point, self.sequential)


@dataclass(frozen=True)
class Voucher(Numbered):
    """The priced kinds; a debit note's lines are its charges."""

    buyer: Buyer
    lines: tuple[Line, ...]
    payments: tuple[Payment, ...] = ()
    modified: Modified | None = None
    extra: tuple[tuple[str, str], ...] = ()

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
        return added_up(self.payments)

    @property
    def party(self) -> Buyer:
        return self.buyer


@dataclass(frozen=True)
class GuideItem:
    description: str
    quantity: Decimal
    code: str | None = None
    extra_code: str | None = None
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Reference:
    """A document named by its SRI type code, such as 01 for an invoice, and its number."""

    document_code: str
    number: str
    issued_on: date
    authorization: str | None = None


@dataclass(frozen=True)
class Recipient:
    identification: str
    name: str
    address: str
    reason: str
    items: tuple[GuideItem, ...]
    route: str | None = None
    destination: str | None = None
    customs_document: str | None = None
    supporting: Reference | None = None


@dataclass(frozen=True)
class RemissionGuide(Numbered):
    carrier: Buyer
    plate: str
    departure: str
    starts_on: date
    ends_on: date
    recipients: tuple[Recipient, ...]
    extra: tuple[tuple[str, str], ...] = ()

    @property
    def party(self) -> Buyer:
        return self.carrier


@dataclass(frozen=True)
class Retention:
    tax: RetentionTax
    code: str
    base: Decimal
    percentage: Decimal
    withheld: Decimal


@dataclass(frozen=True)
class Abroad:
    """A payment abroad, by the ATS catalog's regime and country codes."""

    regime: str
    country: str
    double_taxation: bool
    withheld_by_law: bool
    preferential: bool


@dataclass(frozen=True)
class SupportDocument:
    """The purchase a withholding stands on; support_code is the ATS catalog's."""

    support_code: str
    document: Reference
    taxes: tuple[TaxGroup, ...]
    retentions: tuple[Retention, ...]
    payments: tuple[Payment, ...]
    registered_on: date | None = None
    abroad: Abroad | None = None

    @property
    def subtotal(self) -> Decimal:
        return sum((tax.base for tax in self.taxes), ZERO)

    @property
    def total(self) -> Decimal:
        return self.subtotal + sum((tax.vat for tax in self.taxes), ZERO)

    @property
    def withheld(self) -> Decimal:
        return sum((retention.withheld for retention in self.retentions), ZERO)

    @property
    def payment_forms(self) -> tuple[Payment, ...]:
        return added_up(self.payments)


@dataclass(frozen=True)
class Withholding(Numbered):
    subject: Buyer
    fiscal_period: date
    documents: tuple[SupportDocument, ...]
    related_party: bool = False
    subject_type: SubjectType | None = None
    extra: tuple[tuple[str, str], ...] = ()

    @property
    def withheld(self) -> Decimal:
        return sum((document.withheld for document in self.documents), ZERO)

    @property
    def party(self) -> Buyer:
        return self.subject


AnyVoucher = Voucher | RemissionGuide | Withholding


@dataclass(frozen=True)
class Issued:
    voucher: AnyVoucher
    issuer: Issuer
    authorized_at: datetime
    authorization_number: str
    total: Decimal
    ice: Decimal = ZERO
    irbpnr: Decimal = ZERO
    tip: Decimal | None = None
