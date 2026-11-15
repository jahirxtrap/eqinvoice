from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from functools import cache, wraps
from pathlib import Path
from typing import Annotated, ParamSpec, TypeVar

from mcp.server.mcpserver import Icon, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from eqinvoice import (
    Abroad,
    Buyer,
    CredentialRefused,
    Environment,
    GuideItem,
    Line,
    Payment,
    PaymentForm,
    Recipient,
    Reference,
    RetentionTax,
    SriRegistry,
    SriUnavailable,
    SriWebServices,
    SubjectType,
    SupportDocument,
    Taxpayer,
    VoucherInvalid,
    VoucherKind,
    price_line,
    read_credential,
    tax_on,
    withhold,
)
from eqinvoice.model import ZERO
from eqinvoice.protocol import VAT_RATES, VAT_RETENTION_RATES
from eqinvoice.ride import default_labels
from eqinvoice_mcp.book import Book, NotInBook
from eqinvoice_mcp.desk import Desk, Refused
from eqinvoice_mcp.model import Filed, Previewed, Profile, SalesReport
from eqinvoice_mcp.settings import SettingsMissing, read_settings

NAME = "eqinvoice"
TITLE = "EQinvoice"
ICON = Path(__file__).with_name("icon.svg")
ICON_TYPE = "image/svg+xml"
INSTRUCTIONS = (
    "Electronic vouchers for Ecuador's SRI, issued by the configured issuer: invoices, credit "
    "and debit notes, purchase settlements, remission guides and withholdings. "
    "Every voucher starts as a draft that comes with a printed preview (a PDF path) to show the "
    "user. Issue a draft only after the user approves that preview: an authorized voucher cannot "
    "be deleted, only offset by a credit note. A pending voucher is asked for again with "
    "check_voucher."
)
DEFAULT_POINT = "001"
DEFAULT_PAYMENT_FORM = PaymentForm.WITH_FINANCIAL_SYSTEM
ANTICIPATED = (
    Refused,
    NotInBook,
    SettingsMissing,
    SriUnavailable,
    VoucherInvalid,
    CredentialRefused,
)

Arguments = ParamSpec("Arguments")
Answer = TypeVar("Answer")


def _choices(named: dict[str, str]) -> str:
    return "; ".join(f"{code} = {name}" for code, name in named.items())


VAT_CHOICES = _choices(
    {
        code: default_labels()["subtotalUnrated"].get(code, f"{rate}%")
        for code, rate in VAT_RATES.items()
    }
)
PAYMENT_CHOICES = _choices(default_labels()["paymentForms"])
RETENTION_CHOICES = _choices({code: f"{rate}%" for code, rate in VAT_RETENTION_RATES.items()})

server = MCPServer(
    name=NAME,
    title=TITLE,
    instructions=INSTRUCTIONS,
    icons=[
        Icon(
            src=f"data:{ICON_TYPE};base64,{base64.b64encode(ICON.read_bytes()).decode()}",
            mime_type=ICON_TYPE,
        )
    ],
)


READS = ToolAnnotations(read_only_hint=True, open_world_hint=False)
LOOKS_UP = ToolAnnotations(read_only_hint=True, open_world_hint=True)
KEEPS = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
DRAFTS = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
REMOVES = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)
ISSUES = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
)


def tool(
    annotations: ToolAnnotations,
) -> Callable[[Callable[Arguments, Answer]], Callable[Arguments, Answer]]:
    """A desk tool with its hints, whose anticipated failures reach the client with their reason."""

    def register(function: Callable[Arguments, Answer]) -> Callable[Arguments, Answer]:
        @wraps(function)
        def answered(*arguments: Arguments.args, **named: Arguments.kwargs) -> Answer:
            try:
                return function(*arguments, **named)
            except ANTICIPATED as reason:
                raise ToolError(str(reason)) from reason

        server.tool(annotations=annotations)(answered)
        return function

    return register


class LineRequest(BaseModel):
    """One line as it is asked for; its subtotal and IVA are worked out to the cent."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate_code: Annotated[str, Field(description=f"The SRI's IVA rate code: {VAT_CHOICES}")]
    discount: Decimal = ZERO
    code: Annotated[
        str | None, Field(description="The line's own code; its position if absent")
    ] = None


class ItemRequest(BaseModel):
    description: str
    quantity: Decimal
    code: str | None = None
    extra_code: str | None = None


class RecipientRequest(BaseModel):
    identification: str
    name: str
    address: Annotated[str, Field(description="Where the goods arrive")]
    reason: Annotated[str, Field(description="Why they travel, such as Venta")]
    items: list[ItemRequest]
    invoice_number: Annotated[
        str | None, Field(description="An authorized invoice of the book the goods were sold by")
    ] = None
    route: str | None = None
    destination: Annotated[
        str | None, Field(description="The destination establishment's code, such as 002")
    ] = None
    customs_document: str | None = None


class TaxRequest(BaseModel):
    base: Decimal
    vat_rate_code: Annotated[str, Field(description=f"The SRI's IVA rate code: {VAT_CHOICES}")]


class RetentionRequest(BaseModel):
    tax: Annotated[RetentionTax, Field(description="1 = income tax; 2 = IVA; 6 = ISD")]
    code: Annotated[
        str,
        Field(
            description="The SRI's retention code: the ATS catalog's for income tax and ISD; "
            f"for IVA {RETENTION_CHOICES}"
        ),
    ]
    base: Decimal
    percentage: Annotated[
        Decimal | None, Field(description="Required for income tax and ISD; IVA takes its code's")
    ] = None


class SupportRequest(BaseModel):
    support_code: Annotated[str, Field(description="The ATS catalog's support code, such as 01")]
    number: Annotated[str, Field(description="The purchase's number: 001-001-000000001")]
    issued_on: date
    taxes: list[TaxRequest]
    retentions: list[RetentionRequest]
    document_code: Annotated[str, Field(description="Its SRI type code; 01 is an invoice")] = "01"
    authorization: str | None = None
    registered_on: date | None = None
    payment_form: Annotated[
        PaymentForm, Field(description=f"The SRI's payment form code: {PAYMENT_CHOICES}")
    ] = DEFAULT_PAYMENT_FORM
    abroad: Annotated[Abroad | None, Field(description="Only for a payment abroad")] = None


@cache
def _desk() -> Desk:
    settings = read_settings()
    return Desk(
        Book(settings.book),
        SriRegistry(),
        SriWebServices,
        lambda: read_credential(settings.signature, settings.password),
    )


def _priced(lines: Sequence[LineRequest]) -> tuple[Line, ...]:
    return tuple(
        price_line(
            line.code or str(position),
            line.description,
            line.quantity,
            line.unit_price,
            line.vat_rate_code,
            line.discount,
        )
        for position, line in enumerate(lines, start=1)
    )


def _recipient(request: RecipientRequest) -> Recipient:
    return Recipient(
        identification=request.identification,
        name=request.name,
        address=request.address,
        reason=request.reason,
        items=tuple(
            GuideItem(item.description, item.quantity, item.code, item.extra_code)
            for item in request.items
        ),
        route=request.route,
        destination=request.destination,
        customs_document=request.customs_document,
        supporting=_desk().reference(request.invoice_number) if request.invoice_number else None,
    )


def _support(request: SupportRequest) -> SupportDocument:
    held = SupportDocument(
        support_code=request.support_code,
        document=Reference(
            request.document_code, request.number, request.issued_on, request.authorization
        ),
        taxes=tuple(tax_on(tax.base, tax.vat_rate_code) for tax in request.taxes),
        retentions=tuple(
            withhold(retention.tax, retention.code, retention.base, retention.percentage)
            for retention in request.retentions
        ),
        payments=(),
        registered_on=request.registered_on,
        abroad=request.abroad,
    )
    return replace(held, payments=(Payment(request.payment_form, held.total),))


@tool(LOOKS_UP)
def look_up_taxpayer(ruc: str) -> Taxpayer:
    """A RUC as the SRI's registry holds it: legal name, standing, regime and establishments."""
    return _desk().look_up(ruc)


@tool(KEEPS)
def configure_issuer(
    ruc: str,
    environment: Environment,
    establishment: Annotated[
        str | None,
        Field(description="Its establishment's code, such as 002; the head office if absent"),
    ] = None,
    point: str = DEFAULT_POINT,
    withholding_agent: Annotated[
        str | None,
        Field(description="Its withholding agent resolution, if the SRI's registry lists it so"),
    ] = None,
    special_taxpayer: Annotated[
        str | None,
        Field(description="Its special taxpayer number, if the SRI's registry lists it so"),
    ] = None,
    system_provider_ruc: Annotated[
        str | None,
        Field(description="The RUC of whoever provides the invoicing system, for every voucher"),
    ] = None,
) -> Profile:
    """Who issues: a RUC as the SRI's registry holds it, from that establishment and point."""
    return _desk().configure(
        ruc,
        environment,
        establishment,
        point,
        withholding_agent,
        special_taxpayer,
        system_provider_ruc,
    )


@tool(READS)
def show_issuer() -> Profile:
    """Who issues now, from which establishment and point, and in which environment."""
    return _desk().profile()


@tool(KEEPS)
def register_client(
    identification: Annotated[
        str, Field(description="A RUC, a cédula, a passport or a foreign tax number")
    ],
    email: Annotated[
        str | None, Field(description="Where the client receives its vouchers")
    ] = None,
    name: Annotated[str | None, Field(description="Required for anything but a RUC")] = None,
    address: Annotated[
        str | None, Field(description="Printed on invoices and purchase settlements")
    ] = None,
    foreign: Annotated[
        bool, Field(description="The identification is a foreign tax administration's")
    ] = False,
) -> Buyer:
    """A client, supplier, carrier or withheld party kept in the book; a RUC is named by the SRI."""
    return _desk().register_client(identification, email, name, address, foreign)


@tool(READS)
def list_clients() -> list[Buyer]:
    """Every client in the book."""
    return list(_desk().clients())


@tool(REMOVES)
def forget_client(identification: str) -> list[Buyer]:
    """The client taken out of the book; the clients left come back."""
    return list(_desk().forget_client(identification))


@tool(DRAFTS)
def draft_invoice(
    client: Annotated[str, Field(description="The identification of a client in the book")],
    lines: list[LineRequest],
    payment_form: Annotated[
        PaymentForm, Field(description=f"The SRI's payment form code: {PAYMENT_CHOICES}")
    ] = DEFAULT_PAYMENT_FORM,
) -> Previewed:
    """An invoice drafted for a client, with the voucher it would become and its printed preview."""
    return _desk().draft_invoice(client, _priced(lines), payment_form)


@tool(DRAFTS)
def draft_credit_note(
    invoice_number: Annotated[str, Field(description="An authorized invoice: 001-001-000000001")],
    reason: str,
    lines: Annotated[
        list[LineRequest] | None, Field(description="What is credited; the whole invoice if absent")
    ] = None,
) -> Previewed:
    """A credit note drafted against an authorized invoice, with its printed preview."""
    return _desk().draft_credit_note(invoice_number, reason, _priced(lines or []))


@tool(DRAFTS)
def draft_debit_note(
    invoice_number: Annotated[str, Field(description="An authorized invoice: 001-001-000000001")],
    charges: Annotated[
        list[LineRequest], Field(description="What is charged; each description is its reason")
    ],
    payment_form: Annotated[
        PaymentForm, Field(description=f"The SRI's payment form code: {PAYMENT_CHOICES}")
    ] = DEFAULT_PAYMENT_FORM,
) -> Previewed:
    """A debit note charging more on an authorized invoice, such as interest, with its preview."""
    return _desk().draft_debit_note(invoice_number, _priced(charges), payment_form)


@tool(DRAFTS)
def draft_purchase_settlement(
    supplier: Annotated[str, Field(description="The identification of a supplier in the book")],
    lines: list[LineRequest],
    payment_form: Annotated[
        PaymentForm, Field(description=f"The SRI's payment form code: {PAYMENT_CHOICES}")
    ] = DEFAULT_PAYMENT_FORM,
) -> Previewed:
    """A purchase settlement for a supplier who issues no vouchers, with its printed preview."""
    return _desk().draft_purchase_settlement(supplier, _priced(lines), payment_form)


@tool(DRAFTS)
def draft_remission_guide(
    carrier: Annotated[str, Field(description="The identification of a carrier in the book")],
    plate: str,
    departure: Annotated[str, Field(description="Where the goods leave from")],
    starts_on: date,
    ends_on: date,
    recipients: list[RecipientRequest],
) -> Previewed:
    """A remission guide for goods on the road, to whom and why, with its printed preview."""
    return _desk().draft_remission_guide(
        carrier,
        plate,
        departure,
        starts_on,
        ends_on,
        tuple(_recipient(held) for held in recipients),
    )


@tool(DRAFTS)
def draft_withholding(
    subject: Annotated[str, Field(description="The identification of the withheld in the book")],
    fiscal_period: Annotated[date, Field(description="Any day of the month withheld")],
    documents: list[SupportRequest],
    related_party: bool = False,
    subject_type: Annotated[
        SubjectType | None,
        Field(description="For a foreign identification: 01 = person; 02 = company"),
    ] = None,
) -> Previewed:
    """A withholding voucher over the purchases it stands on, with its printed preview."""
    return _desk().draft_withholding(
        subject,
        fiscal_period,
        tuple(_support(held) for held in documents),
        related_party,
        subject_type,
    )


@tool(READS)
def list_drafts() -> list[Previewed]:
    """Every draft waiting to be issued, with the voucher it would become now."""
    return list(_desk().drafts())


@tool(REMOVES)
def discard_draft(identifier: str) -> list[Previewed]:
    """The draft thrown away; the drafts left come back."""
    return list(_desk().discard(identifier))


@tool(ISSUES)
def issue_draft(identifier: str) -> Filed:
    """The approved draft numbered, signed and sent to the SRI; it comes back as the SRI left it."""
    return _desk().issue(identifier)


@tool(READS)
def list_vouchers() -> list[Filed]:
    """Every voucher the issuer numbered in its current environment, as each stands."""
    return list(_desk().vouchers())


@tool(KEEPS)
def check_voucher(
    number: Annotated[str, Field(description="001-001-000000001")],
    kind: VoucherKind = VoucherKind.INVOICE,
) -> Filed:
    """A pending voucher sent again and asked for at the SRI; any other comes back as it stands."""
    return _desk().check(number, kind)


@tool(READS)
def summarize_sales(
    since: Annotated[date, Field(description="The period's first day, such as 2026-11-01")],
    until: Annotated[date, Field(description="The period's last day, such as 2026-11-30")],
) -> SalesReport:
    """A period's authorized sales per IVA rate, as declared: gross, net of credit notes, IVA."""
    return _desk().sales(since, until)


def main() -> None:
    server.run()
