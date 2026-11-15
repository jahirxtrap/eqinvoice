from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from io import BytesIO
from typing import Any

from reportlab.graphics.barcode.code128 import Code128
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from eqinvoice.document import issuer_legend, money
from eqinvoice.model import Issued, Modified, VoucherKind
from eqinvoice.protocol import (
    CLOCK,
    DATE_FORMAT,
    EMAIL_FIELD,
    SYSTEM_PROVIDER_FIELD,
    buyer_shown,
)
from eqinvoice.ride.labels import Labels, default_labels, filled
from eqinvoice.ride.logo import Logo


class Layout(StrEnum):
    PAGE = "page"
    TICKET = "ticket"


MARGIN = 12 * mm
GAP = 4 * mm
PAD = 6
BORDER = 0.6
CORNER = 6
INK = colors.black
FAINT = colors.Color(0.45, 0.45, 0.45)
RULE = colors.Color(0.78, 0.78, 0.78)
BODY_SIZE = 8
TITLE_SIZE = 13
LEAD_SIZE = 10
LEADING = 1.3
LOGO_HEIGHT = 16 * mm
UNRATED = ("0", "6", "7")
BARCODE_HEIGHT = 12 * mm
BARCODE_BAR = 0.62
DETAIL_SHARES = (0.14, 0.1, 0.38, 0.14, 0.11, 0.13)
NUMBER_COLUMNS = 3
EXTRA_SHARES = (0.3, 0.7)
PAYMENT_SHARES = (0.76, 0.24)
TOTAL_SHARES = (0.64, 0.36)
FOOT_SHARE = 0.58
BUYER_SHARE = 0.62
UNIT_PRICE_PLACES = 2
INSTANT = "%d/%m/%Y %H:%M:%S"
TICKET_WIDTH = 80 * mm
TICKET_MARGIN = 4 * mm
TICKET_LOGO_HEIGHT = 11 * mm
TICKET_TITLE_SIZE = 11
TICKET_QR = 28 * mm
TICKET_LINE_SHARES = (0.72, 0.28)
TICKET_TOTAL_SHARES = (0.72, 0.28)
TICKET_TAIL = 8 * mm
FRAME_PADDING = 6
UNBOUNDED = 10_000 * mm
BARCODE_ROWS = (9, 10)
BARE = TableStyle(
    [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]
)


def _style(
    size: int = BODY_SIZE, bold: bool = False, muted: bool = False, align: int = TA_LEFT
) -> ParagraphStyle:
    return ParagraphStyle(
        name=f"ride-{size}-{bold}-{muted}-{align}",
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        leading=size * LEADING,
        textColor=FAINT if muted else INK,
        alignment=align,
    )


def _escaped(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(
    value: str,
    size: int = BODY_SIZE,
    bold: bool = False,
    muted: bool = False,
    align: int = TA_LEFT,
) -> Paragraph:
    return Paragraph(_escaped(value), _style(size, bold, muted, align))


def _field(label: str, value: str, align: int = TA_LEFT, size: int = BODY_SIZE) -> Paragraph:
    return Paragraph(f"<b>{_escaped(label)}:</b> {_escaped(value)}", _style(size, align=align))


def _amount(value: str, bold: bool = False, size: int = BODY_SIZE) -> Paragraph:
    return _text(value, size, bold=bold, align=TA_RIGHT)


def _quantity(amount: Decimal) -> str:
    return f"{amount.normalize():f}" if amount == amount.to_integral_value() else f"{amount:.2f}"


def _unit_price(amount: Decimal) -> str:
    exact = f"{amount:.6f}".rstrip("0")
    places = len(exact.partition(".")[2])
    return f"{amount:.{max(places, UNIT_PRICE_PLACES)}f}"


def _widths(width: float, shares: tuple[float, ...]) -> list[float]:
    return [width * share for share in shares]


def _tall(flowable: Flowable, width: float) -> float:
    return float(flowable.wrap(width, UNBOUNDED)[1])


def _box(
    rows: list[list[Any]],
    widths: list[float],
    lines: list[tuple[Any, ...]] | None = None,
    at_least: float = 0.0,
) -> Table:
    style = [
        ("BOX", (0, 0), (-1, -1), BORDER, INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), PAD),
        ("RIGHTPADDING", (0, 0), (-1, -1), PAD),
        ("TOPPADDING", (0, 0), (-1, -1), PAD / 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), PAD / 2),
        *(lines or []),
    ]
    table = Table(rows, colWidths=widths, cornerRadii=[CORNER] * 4)
    table.setStyle(TableStyle(style))
    short = at_least - _tall(table, sum(widths))
    if short <= 0:
        return table
    stretched = Table(
        [*rows, [""] * len(widths)],
        colWidths=widths,
        rowHeights=[None] * len(rows) + [short],
        cornerRadii=[CORNER] * 4,
    )
    stretched.setStyle(TableStyle(style))
    return stretched


def _divided(last: int) -> list[tuple[Any, ...]]:
    return [("LINEBELOW", (0, 0), (-1, last - 1), BORDER, RULE)] if last > 0 else []


def _keeps_accounts(issued: Issued, words: Labels) -> str:
    return str(words["yes"] if issued.issuer.keeps_accounts else words["no"])


def _issuer_rows(issued: Issued, words: Labels) -> list[list[Any]]:
    issuer = issued.issuer
    rows: list[list[Any]] = [[_text(issuer.legal_name, LEAD_SIZE, bold=True)]]
    if issuer.trade_name:
        rows.append([_text(issuer.trade_name, bold=True)])
    rows += [
        [_field(words["headOffice"], issuer.head_office_address)],
        [_field(words["branch"], issued.voucher.establishment_address)],
    ]
    if issuer.special_taxpayer:
        rows.append([_field(words["specialTaxpayer"], issuer.special_taxpayer)])
    rows.append([_field(words["keepsAccounts"], _keeps_accounts(issued, words))])
    if issuer.withholding_agent:
        rows.append([_field(words["withholdingAgent"], issuer.withholding_agent)])
    if legend := issuer_legend(issuer):
        rows.append([_text(legend, bold=True)])
    return rows


def _authorized(issued: Issued) -> str:
    return issued.authorized_at.astimezone(CLOCK).strftime(INSTANT)


def _voucher_rows(issued: Issued, words: Labels) -> list[list[Any]]:
    voucher = issued.voucher
    return [
        [_field(words["ruc"], issued.issuer.ruc, size=LEAD_SIZE)],
        [_text(words["documents"][voucher.kind], TITLE_SIZE, bold=True)],
        [_text(f"{words['number']} {voucher.number}", LEAD_SIZE, bold=True)],
        [_text(words["authorizationNumber"], bold=True)],
        [_text(issued.authorization_number)],
        [_field(words["authorizedAt"], _authorized(issued))],
        [_field(words["environment"], words["environments"][voucher.environment])],
        [_field(words["emission"], words["normalEmission"])],
        [_text(words["accessKey"], bold=True)],
        [Code128(voucher.access_key, barHeight=BARCODE_HEIGHT, barWidth=BARCODE_BAR)],
        [_text(voucher.access_key, align=TA_CENTER)],
    ]


def _head(issued: Issued, words: Labels, width: float, logo: Logo | None) -> Table:
    column = (width - GAP) / 2
    left: list[Flowable] = []
    if logo is not None:
        drawn = logo.flowable(LOGO_HEIGHT, one_ink=False)
        drawn.hAlign = "CENTER"
        left = [drawn, Spacer(1, GAP)]
    above = sum(_tall(item, column) for item in left)
    issuer_rows = _issuer_rows(issued, words)
    voucher_rows = _voucher_rows(issued, words)
    centred = [("ALIGN", (0, BARCODE_ROWS[0]), (0, BARCODE_ROWS[1]), "CENTER")]
    voucher = _box(voucher_rows, [column], centred)
    issuer = _box(issuer_rows, [column], at_least=_tall(voucher, column) - above)
    voucher = _box(voucher_rows, [column], centred, at_least=above + _tall(issuer, column))
    head = Table([[[*left, issuer], "", voucher]], colWidths=[column, GAP, column])
    head.setStyle(BARE)
    return head


def _modified_name(modified: Modified, words: Labels) -> str:
    return f"{words['documents'][VoucherKind.INVOICE]} {modified.number}"


def _buyer_fields(issued: Issued, words: Labels) -> list[Paragraph]:
    voucher = issued.voucher
    buyer = voucher.buyer
    name, identification = buyer_shown(buyer.kind, buyer.name, buyer.identification)
    return [
        _field(words["buyerName"], name),
        _field(words["buyerId"], identification),
        _field(words["issuedOn"], voucher.issued_on.strftime(DATE_FORMAT)),
    ]


def _modified_fields(modified: Modified, words: Labels) -> list[Paragraph]:
    return [
        _field(words["modified"], _modified_name(modified, words)),
        _field(words["modifiedIssuedOn"], modified.issued_on.strftime(DATE_FORMAT)),
        _field(words["reason"], modified.reason),
    ]


def _buyer_box(issued: Issued, words: Labels, width: float) -> Table:
    name, identification, issued_on = _buyer_fields(issued, words)
    rows: list[list[Any]] = [[name, identification], [issued_on, ""]]
    modified = issued.voucher.modified
    if modified is not None:
        which, when, why = _modified_fields(modified, words)
        rows += [[which, when], [why, ""]]
    return _box(rows, [width * BUYER_SHARE, width * (1 - BUYER_SHARE)])


def _details(issued: Issued, words: Labels, width: float) -> Table:
    columns = words["columns"]
    names = ("code", "quantity", "description", "unitPrice", "discount", "total")
    header = [
        _text(columns[name], bold=True, align=TA_RIGHT if index >= NUMBER_COLUMNS else TA_LEFT)
        for index, name in enumerate(names)
    ]
    rows = [header] + [
        [
            _text(line.code),
            _text(_quantity(line.quantity)),
            _text(line.description),
            _amount(_unit_price(line.unit_price)),
            _amount(money(line.discount)),
            _amount(money(line.subtotal)),
        ]
        for line in issued.voucher.lines
    ]
    lines = [*_divided(len(rows) - 1), ("LINEBELOW", (0, 0), (-1, 0), BORDER, INK)]
    return _box(rows, _widths(width, DETAIL_SHARES), lines)


def _total_rows(issued: Issued, words: Labels) -> list[list[Any]]:
    voucher = issued.voucher
    groups = voucher.tax_groups
    rated = [group for group in groups if group.rate > 0]
    unrated = {group.code: group.base for group in groups if group.rate == 0}
    labels = words["subtotalUnrated"]
    rows: list[list[Any]] = [
        [
            _text(filled(words["subtotalRated"], rate=_quantity(group.rate))),
            _amount(money(group.base)),
        ]
        for group in rated
    ]
    rows += [
        [_text(labels.get(code, labels[UNRATED[0]])), _amount(money(unrated.get(code, Decimal(0))))]
        for code in dict.fromkeys([*UNRATED, *unrated])
    ]
    rows.append([_text(words["subtotal"]), _amount(money(voucher.subtotal))])
    rows.append([_text(words["discount"]), _amount(money(voucher.discount))])
    rows.append([_text(words["ice"]), _amount(money(issued.ice))])
    rows += [
        [_text(filled(words["vatRated"], rate=_quantity(group.rate))), _amount(money(group.vat))]
        for group in rated
    ]
    rows.append([_text(words["irbpnr"]), _amount(money(issued.irbpnr))])
    if issued.tip is not None:
        rows.append([_text(words["tip"]), _amount(money(issued.tip))])
    rows.append(
        [
            _text(words["total"], LEAD_SIZE, bold=True),
            _amount(money(issued.total), bold=True, size=LEAD_SIZE),
        ]
    )
    return rows


def _totals(
    issued: Issued,
    words: Labels,
    width: float,
    shares: tuple[float, ...] = TOTAL_SHARES,
    at_least: float = 0.0,
) -> Table:
    rows = _total_rows(issued, words)
    last = len(rows) - 1
    lines = [*_divided(last - 1), ("LINEBELOW", (0, last - 1), (-1, last - 1), BORDER, INK)]
    return _box(rows, _widths(width, shares), lines, at_least)


def _extra_box(issued: Issued, words: Labels, width: float, at_least: float = 0.0) -> Table:
    fields = list(issued.voucher.extra)
    if issued.issuer.system_provider_ruc:
        fields.insert(0, (SYSTEM_PROVIDER_FIELD, issued.issuer.system_provider_ruc))
    if issued.voucher.buyer.email:
        fields.insert(0, (EMAIL_FIELD, issued.voucher.buyer.email))
    rows: list[list[Any]] = [[_text(words["extra"], bold=True), ""]]
    rows += [[_text(name), _text(value)] for name, value in fields]
    lines = [("SPAN", (0, 0), (-1, 0)), *_divided(len(rows) - 1)]
    return _box(rows, _widths(width, EXTRA_SHARES), lines, at_least)


def _payment_box(issued: Issued, words: Labels, width: float) -> Table | None:
    payments = issued.voucher.payment_forms
    if not payments:
        return None
    rows: list[list[Any]] = [
        [_text(words["paymentForm"], bold=True), _text(words["value"], bold=True, align=TA_RIGHT)]
    ]
    rows += [
        [
            _text(f"{paid.form} - {words['paymentForms'].get(paid.form, '')}"),
            _amount(money(paid.amount)),
        ]
        for paid in payments
    ]
    return _box(rows, _widths(width, PAYMENT_SHARES), _divided(len(rows) - 1))


def _foot(issued: Issued, words: Labels, width: float) -> Table:
    side = (width - GAP) * FOOT_SHARE
    rest = width - GAP - side
    totals = _totals(issued, words, rest)
    payment = _payment_box(issued, words, side)
    below = _tall(payment, side) + GAP if payment is not None else 0.0
    extra = _extra_box(issued, words, side, _tall(totals, rest) - below)
    totals = _totals(issued, words, rest, at_least=_tall(extra, side) + below)
    left: list[Flowable] = [extra] if payment is None else [extra, Spacer(1, GAP), payment]
    foot = Table([[left, "", totals]], colWidths=[side, GAP, rest])
    foot.setStyle(BARE)
    return foot


def _page(issued: Issued, words: Labels, logo: Logo | None) -> bytes:
    buffer = BytesIO()
    page = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=f"{words['documents'][issued.voucher.kind]} {issued.voucher.number}",
    )
    width = page.width
    page.build(
        [
            _head(issued, words, width, logo),
            Spacer(1, GAP),
            _buyer_box(issued, words, width),
            Spacer(1, GAP),
            _details(issued, words, width),
            Spacer(1, GAP),
            _foot(issued, words, width),
        ]
    )
    return buffer.getvalue()


def _rule(width: float) -> Table:
    table = Table([[""]], colWidths=[width], rowHeights=[GAP])
    table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), BORDER, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _ticket_issuer(issued: Issued, words: Labels, logo: Logo | None) -> list[Flowable]:
    issuer = issued.issuer
    flow: list[Flowable] = []
    if logo is not None:
        drawn = logo.flowable(TICKET_LOGO_HEIGHT, one_ink=True)
        drawn.hAlign = "CENTER"
        flow = [drawn, Spacer(1, GAP / 2)]
    flow.append(_text(issuer.legal_name, bold=True, align=TA_CENTER))
    if issuer.trade_name:
        flow.append(_text(issuer.trade_name, align=TA_CENTER))
    flow += [
        _field(words["ruc"], issuer.ruc, align=TA_CENTER),
        Spacer(1, GAP / 2),
        _field(words["headOffice"], issuer.head_office_address),
        _field(words["branch"], issued.voucher.establishment_address),
        _field(words["keepsAccounts"], _keeps_accounts(issued, words)),
    ]
    if issuer.special_taxpayer:
        flow.append(_field(words["specialTaxpayer"], issuer.special_taxpayer))
    if issuer.withholding_agent:
        flow.append(_field(words["withholdingAgent"], issuer.withholding_agent))
    if legend := issuer_legend(issuer):
        flow.append(_text(legend, bold=True))
    return flow


def _qr(value: str) -> Drawing:
    code = QrCodeWidget(value)
    left, bottom, right, top = code.getBounds()
    drawing = Drawing(
        TICKET_QR,
        TICKET_QR,
        transform=[TICKET_QR / (right - left), 0, 0, TICKET_QR / (top - bottom), 0, 0],
    )
    drawing.add(code)
    drawing.hAlign = "CENTER"
    return drawing


def _ticket_voucher(issued: Issued, words: Labels) -> list[Flowable]:
    voucher = issued.voucher
    return [
        _text(words["documents"][voucher.kind], TICKET_TITLE_SIZE, bold=True, align=TA_CENTER),
        _text(f"{words['number']} {voucher.number}", bold=True, align=TA_CENTER),
        Spacer(1, GAP / 2),
        _field(words["environment"], words["environments"][voucher.environment]),
        _field(words["emission"], words["normalEmission"]),
        _field(words["authorizedAt"], _authorized(issued)),
        _text(words["authorizationNumber"], bold=True),
        _text(issued.authorization_number),
        _text(words["accessKey"], bold=True),
        _text(voucher.access_key),
        Spacer(1, GAP / 2),
        _qr(voucher.access_key),
    ]


def _ticket_lines(issued: Issued, words: Labels, width: float) -> Table:
    columns = words["columns"]
    rows: list[list[Any]] = [
        [
            _text(columns["description"], bold=True),
            _text(columns["total"], bold=True, align=TA_RIGHT),
        ]
    ]
    for line in issued.voucher.lines:
        priced = f"{_quantity(line.quantity)} x {_unit_price(line.unit_price)}"
        if line.discount > 0:
            priced = f"{priced} - {columns['discount']} {money(line.discount)}"
        rows.append([_text(line.description), _amount(money(line.subtotal))])
        rows.append([_text(f"{line.code} • {priced}", muted=True), ""])
    table = Table(rows, colWidths=_widths(width, TICKET_LINE_SHARES))
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("LINEBELOW", (0, 0), (-1, 0), BORDER, INK),
            ]
        )
    )
    return table


def _ticket(issued: Issued, words: Labels, logo: Logo | None) -> bytes:
    width = TICKET_WIDTH - 2 * TICKET_MARGIN - 2 * FRAME_PADDING
    modified = issued.voucher.modified
    payment = _payment_box(issued, words, width)
    flow: list[Flowable] = [
        *_ticket_issuer(issued, words, logo),
        _rule(width),
        *_ticket_voucher(issued, words),
        _rule(width),
        *_buyer_fields(issued, words),
        *(_modified_fields(modified, words) if modified is not None else []),
        _rule(width),
        _ticket_lines(issued, words, width),
        Spacer(1, GAP),
        _totals(issued, words, width, TICKET_TOTAL_SHARES),
        Spacer(1, GAP),
        _extra_box(issued, words, width),
        *([] if payment is None else [Spacer(1, GAP), payment]),
    ]
    tall = sum(_tall(item, width) for item in flow) + 2 * (TICKET_MARGIN + FRAME_PADDING)
    buffer = BytesIO()
    strip = SimpleDocTemplate(
        buffer,
        pagesize=(TICKET_WIDTH, tall + TICKET_TAIL),
        leftMargin=TICKET_MARGIN,
        rightMargin=TICKET_MARGIN,
        topMargin=TICKET_MARGIN,
        bottomMargin=TICKET_MARGIN,
        title=f"{words['documents'][issued.voucher.kind]} {issued.voucher.number}",
    )
    strip.build(flow)
    return buffer.getvalue()


def render(
    issued: Issued,
    layout: Layout = Layout.PAGE,
    logo: Logo | None = None,
    labels: Labels | None = None,
) -> bytes:
    words = labels or default_labels()
    if layout is Layout.TICKET:
        return _ticket(issued, words, logo)
    return _page(issued, words, logo)
