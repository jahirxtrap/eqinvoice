from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
from decimal import Decimal
from functools import cache, wraps
from pathlib import Path
from typing import Annotated, ParamSpec, TypeVar

from mcp.server.mcpserver import Icon, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from eqinvoice import (
    Buyer,
    CredentialRefused,
    Environment,
    Line,
    SriRegistry,
    SriUnavailable,
    SriWebServices,
    Taxpayer,
    VoucherInvalid,
    VoucherKind,
    price_line,
    read_credential,
)
from eqinvoice.model import ZERO
from eqinvoice.protocol import VAT_RATES
from eqinvoice.ride import default_labels
from eqinvoice_mcp.book import Book, NotInBook
from eqinvoice_mcp.desk import Desk, Refused
from eqinvoice_mcp.model import Filed, Previewed, Profile
from eqinvoice_mcp.settings import SettingsMissing, read_settings

NAME = "eqinvoice"
TITLE = "EQinvoice"
ICON = Path(__file__).with_name("icon.svg")
ICON_TYPE = "image/svg+xml"
INSTRUCTIONS = (
    "Electronic invoices and credit notes for Ecuador's SRI, issued by the configured issuer. "
    "Every voucher starts as a draft that comes with a printed preview (a PDF path) to show the "
    "user. Issue a draft only after the user approves that preview: an authorized voucher cannot "
    "be deleted, only offset by a credit note. A pending voucher is asked for again with "
    "check_voucher."
)
DEFAULT_ESTABLISHMENT = "001"
DEFAULT_POINT = "001"
DEFAULT_PAYMENT_FORM = "20"
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


@tool(LOOKS_UP)
def look_up_taxpayer(ruc: str) -> Taxpayer:
    """A RUC as the SRI's registry holds it: legal name, standing, regime and establishments."""
    return _desk().look_up(ruc)


@tool(KEEPS)
def configure_issuer(
    ruc: str,
    environment: Environment,
    establishment: str = DEFAULT_ESTABLISHMENT,
    point: str = DEFAULT_POINT,
) -> Profile:
    """Who issues: a RUC as the SRI's registry holds it, issuing from that point and environment."""
    return _desk().configure(ruc, environment, establishment, point)


@tool(READS)
def show_issuer() -> Profile:
    """Who issues now, from which establishment and point, and in which environment."""
    return _desk().profile()


@tool(KEEPS)
def register_client(
    identification: Annotated[str, Field(description="A RUC, a cédula or a passport number")],
    email: Annotated[
        str | None, Field(description="Where the client receives its vouchers")
    ] = None,
    name: Annotated[str | None, Field(description="Required for a cédula or a passport")] = None,
) -> Buyer:
    """A client kept in the book; a RUC is named as the SRI's registry holds it."""
    return _desk().register_client(identification, email, name)


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
        str, Field(description=f"The SRI's payment form code: {PAYMENT_CHOICES}")
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


def main() -> None:
    server.run()
