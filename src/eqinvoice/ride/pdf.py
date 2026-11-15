from __future__ import annotations

from datetime import date
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
from eqinvoice.model import (
    Buyer,
    GuideItem,
    Issued,
    Modified,
    Payment,
    Recipient,
    RemissionGuide,
    Voucher,
    VoucherKind,
    Withholding,
)
from eqinvoice.protocol import (
    CLOCK,
    DATE_FORMAT,
    DOCUMENT_CODES,
    EMAIL_FIELD,
    FISCAL_PERIOD_FORMAT,
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
CHARGE_SHARES = (0.76, 0.24)
ITEM_SHARES = (0.12, 0.52, 0.18, 0.18)
RETENTION_SHARES = (0.13, 0.16, 0.11, 0.1, 0.1, 0.14, 0.12, 0.14)
SEPARATOR = " • "
RETENTION_COLUMNS = ("document", "number", "issuedOn", "fiscalYear", "tax", "base", "percentage")
RETENTION_AMOUNTS = RETENTION_COLUMNS.index("base")
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


def _day(on: date) -> str:
    return on.strftime(DATE_FORMAT)


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


def _pairs(groups: list[list[Paragraph]], width: float) -> Table:
    rows: list[list[Any]] = [
        [*group[at : at + 2], *[""] * (2 - len(group[at : at + 2]))]
        for group in groups
        for at in range(0, len(group), 2)
    ]
    return _box(rows, [width * BUYER_SHARE, width * (1 - BUYER_SHARE)])


def _grid(header: list[str], rows: list[list[Any]], widths: list[float], amounts: int) -> Table:
    titles = [
        _text(name, bold=True, align=TA_RIGHT if index >= amounts else TA_LEFT)
        for index, name in enumerate(header)
    ]
    body = [titles, *rows]
    lines = [*_divided(len(body) - 1), ("LINEBELOW", (0, 0), (-1, 0), BORDER, INK)]
    return _box(body, widths, lines)


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


def _document_name(code: str, number: str, words: Labels) -> str:
    return f"{words['documentCodes'].get(code, code)} {number}"


def _party_fields(party: Buyer, words: Labels, name_label: str, id_label: str) -> list[Paragraph]:
    name, identification = buyer_shown(party.kind, party.name, party.identification)
    return [_field(words[name_label], name), _field(words[id_label], identification)]


def _buyer_fields(voucher: Voucher, words: Labels) -> list[Paragraph]:
    fields = _party_fields(voucher.buyer, words, "buyerName", "buyerId")
    fields.append(_field(words["issuedOn"], _day(voucher.issued_on)))
    if voucher.buyer.address:
        fields.append(_field(words["address"], voucher.buyer.address))
    return fields


def _modified_fields(modified: Modified, words: Labels) -> list[Paragraph]:
    fields = [
        _field(
            words["modified"],
            _document_name(DOCUMENT_CODES[VoucherKind.INVOICE], modified.number, words),
        ),
        _field(words["modifiedIssuedOn"], _day(modified.issued_on)),
    ]
    if modified.reason:
        fields.append(_field(words["reason"], modified.reason))
    return fields


def _carrier_fields(guide: RemissionGuide, words: Labels) -> list[Paragraph]:
    return [
        *_party_fields(guide.carrier, words, "carrierName", "carrierId"),
        _field(words["plate"], guide.plate),
        _field(words["departure"], guide.departure),
        _field(words["startsOn"], _day(guide.starts_on)),
        _field(words["endsOn"], _day(guide.ends_on)),
    ]


def _recipient_fields(recipient: Recipient, words: Labels) -> list[Paragraph]:
    fields = []
    supporting = recipient.supporting
    if supporting is not None:
        fields += [
            _field(
                words["supporting"],
                _document_name(supporting.document_code, supporting.number, words),
            ),
            _field(words["supportingIssuedOn"], _day(supporting.issued_on)),
        ]
        if supporting.authorization:
            fields.append(_field(words["supportingAuthorization"], supporting.authorization))
    fields += [
        _field(words["transferReason"], recipient.reason),
        _field(words["destination"], recipient.address),
        _field(words["recipientId"], recipient.identification),
        _field(words["recipientName"], recipient.name),
    ]
    optional = (
        ("customsDocument", recipient.customs_document),
        ("destinationEstablishment", recipient.destination),
        ("route", recipient.route),
    )
    return fields + [_field(words[label], value) for label, value in optional if value]


def _subject_fields(held: Withholding, words: Labels) -> list[Paragraph]:
    return [
        *_party_fields(held.subject, words, "buyerName", "buyerId"),
        _field(words["issuedOn"], _day(held.issued_on)),
        _field(words["fiscalPeriod"], held.fiscal_period.strftime(FISCAL_PERIOD_FORMAT)),
    ]


def _details(voucher: Voucher, words: Labels, width: float) -> Table:
    if voucher.kind == VoucherKind.DEBIT_NOTE:
        charges = words["charges"]
        rows = [[_text(line.description), _amount(money(line.subtotal))] for line in voucher.lines]
        return _grid([charges["reason"], charges["value"]], rows, _widths(width, CHARGE_SHARES), 1)
    columns = words["columns"]
    names = ("code", "quantity", "description", "unitPrice", "discount", "total")
    rows = [
        [
            _text(line.code),
            _text(_quantity(line.quantity)),
            _text(line.description),
            _amount(_unit_price(line.unit_price)),
            _amount(money(line.discount)),
            _amount(money(line.subtotal)),
        ]
        for line in voucher.lines
    ]
    return _grid(
        [columns[name] for name in names], rows, _widths(width, DETAIL_SHARES), NUMBER_COLUMNS
    )


def _described(item: GuideItem) -> str:
    return SEPARATOR.join([item.description, *(f"{name}: {value}" for name, value in item.details)])


def _items(recipient: Recipient, words: Labels, width: float) -> Table:
    columns = words["items"]
    rows = [
        [
            _text(_quantity(item.quantity)),
            _text(_described(item)),
            _text(item.code or ""),
            _text(item.extra_code or ""),
        ]
        for item in recipient.items
    ]
    header = [columns[name] for name in ("quantity", "description", "code", "extraCode")]
    return _grid(header, rows, _widths(width, ITEM_SHARES), len(header))


def _retention_entries(held: Withholding, words: Labels) -> list[tuple[str, ...]]:
    period = held.fiscal_period.strftime(FISCAL_PERIOD_FORMAT)
    return [
        (
            words["documentCodes"].get(
                support.document.document_code, support.document.document_code
            ),
            support.document.number,
            _day(support.document.issued_on),
            period,
            words["retentionTaxes"].get(retention.tax, retention.tax),
            money(retention.base),
            _quantity(retention.percentage),
            money(retention.withheld),
        )
        for support in held.documents
        for retention in support.retentions
    ]


def _retentions(held: Withholding, words: Labels, width: float) -> Table:
    columns = words["retentions"]
    rows = [
        [
            _amount(value) if index >= RETENTION_AMOUNTS else _text(value)
            for index, value in enumerate(entry)
        ]
        for entry in _retention_entries(held, words)
    ]
    header = [columns[name] for name in (*RETENTION_COLUMNS, "withheld")]
    return _grid(header, rows, _widths(width, RETENTION_SHARES), RETENTION_AMOUNTS)


def _priced_totals(issued: Issued, voucher: Voucher, words: Labels) -> list[list[Any]]:
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
    rows.append(_total_row(words["total"], issued.total))
    return rows


def _total_row(label: str, total: Decimal) -> list[Any]:
    return [_text(label, LEAD_SIZE, bold=True), _amount(money(total), bold=True, size=LEAD_SIZE)]


def _total_rows(issued: Issued, words: Labels) -> list[list[Any]]:
    voucher = issued.voucher
    if isinstance(voucher, Voucher):
        return _priced_totals(issued, voucher, words)
    if isinstance(voucher, Withholding):
        return [_total_row(words["withheldTotal"], voucher.withheld)]
    return []


def _totals(
    issued: Issued,
    words: Labels,
    width: float,
    shares: tuple[float, ...] = TOTAL_SHARES,
    at_least: float = 0.0,
) -> Table | None:
    rows = _total_rows(issued, words)
    if not rows:
        return None
    last = len(rows) - 1
    lines = [*_divided(last - 1), ("LINEBELOW", (0, last - 1), (-1, last - 1), BORDER, INK)]
    return _box(rows, _widths(width, shares), lines if last else [], at_least)


def _extra_box(issued: Issued, words: Labels, width: float, at_least: float = 0.0) -> Table:
    voucher = issued.voucher
    fields = list(voucher.extra)
    if issued.issuer.system_provider_ruc:
        fields.insert(0, (SYSTEM_PROVIDER_FIELD, issued.issuer.system_provider_ruc))
    if voucher.party.email:
        fields.insert(0, (EMAIL_FIELD, voucher.party.email))
    rows: list[list[Any]] = [[_text(words["extra"], bold=True), ""]]
    rows += [[_text(name), _text(value)] for name, value in fields]
    lines = [("SPAN", (0, 0), (-1, 0)), *_divided(len(rows) - 1)]
    return _box(rows, _widths(width, EXTRA_SHARES), lines, at_least)


def _payment_box(payments: tuple[Payment, ...], words: Labels, width: float) -> Table | None:
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


def _payments(issued: Issued) -> tuple[Payment, ...]:
    voucher = issued.voucher
    return voucher.payment_forms if isinstance(voucher, Voucher) else ()


def _foot(issued: Issued, words: Labels, width: float) -> Table:
    side = (width - GAP) * FOOT_SHARE
    rest = width - GAP - side
    totals = _totals(issued, words, rest)
    if totals is None:
        return _extra_box(issued, words, width)
    payment = _payment_box(_payments(issued), words, side)
    below = _tall(payment, side) + GAP if payment is not None else 0.0
    extra = _extra_box(issued, words, side, _tall(totals, rest) - below)
    totals = _totals(issued, words, rest, at_least=_tall(extra, side) + below)
    left: list[Flowable] = [extra] if payment is None else [extra, Spacer(1, GAP), payment]
    foot = Table([[left, "", totals]], colWidths=[side, GAP, rest])
    foot.setStyle(BARE)
    return foot


def _body(issued: Issued, words: Labels, width: float) -> list[Flowable]:
    voucher = issued.voucher
    if isinstance(voucher, RemissionGuide):
        flow: list[Flowable] = [_pairs([_carrier_fields(voucher, words)], width)]
        for recipient in voucher.recipients:
            flow += [
                Spacer(1, GAP),
                _pairs([_recipient_fields(recipient, words)], width),
                Spacer(1, GAP),
                _items(recipient, words, width),
            ]
        return flow
    if isinstance(voucher, Withholding):
        return [
            _pairs([_subject_fields(voucher, words)], width),
            Spacer(1, GAP),
            _retentions(voucher, words, width),
        ]
    modified = [_modified_fields(voucher.modified, words)] if voucher.modified else []
    return [
        _pairs([_buyer_fields(voucher, words), *modified], width),
        Spacer(1, GAP),
        _details(voucher, words, width),
    ]


def _title(issued: Issued, words: Labels) -> str:
    return f"{words['documents'][issued.voucher.kind]} {issued.voucher.number}"


def _page(issued: Issued, words: Labels, logo: Logo | None) -> bytes:
    buffer = BytesIO()
    page = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=_title(issued, words),
    )
    width = page.width
    page.build(
        [
            _head(issued, words, width, logo),
            Spacer(1, GAP),
            *_body(issued, words, width),
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


def _ticket_list(
    header: tuple[str, str], entries: list[tuple[str, str, str]], width: float
) -> Table:
    rows: list[list[Any]] = [
        [_text(header[0], bold=True), _text(header[1], bold=True, align=TA_RIGHT)]
    ]
    for main, amount, below in entries:
        rows.append([_text(main), _amount(amount)])
        if below:
            rows.append([_text(below, muted=True), ""])
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


def _priced_entries(voucher: Voucher, words: Labels) -> list[tuple[str, str, str]]:
    columns = words["columns"]
    entries = []
    for line in voucher.lines:
        priced = f"{_quantity(line.quantity)} x {_unit_price(line.unit_price)}"
        if line.discount > 0:
            priced = f"{priced} - {columns['discount']} {money(line.discount)}"
        below = (
            "" if voucher.kind == VoucherKind.DEBIT_NOTE else SEPARATOR.join([line.code, priced])
        )
        entries.append((line.description, money(line.subtotal), below))
    return entries


def _ticket_body(issued: Issued, words: Labels, width: float) -> list[Flowable]:
    voucher = issued.voucher
    columns = words["columns"]
    if isinstance(voucher, RemissionGuide):
        flow: list[Flowable] = list(_carrier_fields(voucher, words))
        for recipient in voucher.recipients:
            entries = [
                (_described(item), _quantity(item.quantity), item.code or "")
                for item in recipient.items
            ]
            flow += [
                _rule(width),
                *_recipient_fields(recipient, words),
                Spacer(1, GAP / 2),
                _ticket_list((columns["description"], columns["quantity"]), entries, width),
            ]
        return flow
    if isinstance(voucher, Withholding):
        retentions = words["retentions"]
        entries = [
            (
                SEPARATOR.join([f"{tax} {percentage}%", f"{document} {number}"]),
                withheld,
                f"{retentions['base']} {base}",
            )
            for document, number, _, _, tax, base, percentage, withheld in _retention_entries(
                voucher, words
            )
        ]
        return [
            *_subject_fields(voucher, words),
            _rule(width),
            _ticket_list((retentions["tax"], retentions["withheld"]), entries, width),
        ]
    modified = _modified_fields(voucher.modified, words) if voucher.modified else []
    return [
        *_buyer_fields(voucher, words),
        *modified,
        _rule(width),
        _ticket_list(
            (columns["description"], columns["total"]), _priced_entries(voucher, words), width
        ),
    ]


def _ticket(issued: Issued, words: Labels, logo: Logo | None) -> bytes:
    width = TICKET_WIDTH - 2 * TICKET_MARGIN - 2 * FRAME_PADDING
    payment = _payment_box(_payments(issued), words, width)
    totals = _totals(issued, words, width, TICKET_TOTAL_SHARES)
    flow: list[Flowable] = [
        *_ticket_issuer(issued, words, logo),
        _rule(width),
        *_ticket_voucher(issued, words),
        _rule(width),
        *_ticket_body(issued, words, width),
        Spacer(1, GAP),
        *([] if totals is None else [totals, Spacer(1, GAP)]),
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
        title=_title(issued, words),
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
