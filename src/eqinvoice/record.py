from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from lxml import etree

from eqinvoice.errors import VoucherInvalid
from eqinvoice.model import (
    ZERO,
    Buyer,
    BuyerKind,
    Environment,
    Issued,
    Issuer,
    Line,
    Modified,
    Payment,
    Voucher,
    VoucherKind,
)
from eqinvoice.protocol import (
    BUYER_CODES,
    CODE_TAGS,
    DATE_FORMAT,
    EMAIL_FIELD,
    ENVIRONMENT_CODES,
    ICE_TAX,
    INFO,
    IRBPNR_TAX,
    PARSER,
    RIMPE_TYPE,
    ROOTS,
    SYSTEM_PROVIDER_FIELD,
    YES,
)
from eqinvoice.protocol import text_at as _text

TOTAL_TAGS = {VoucherKind.INVOICE: "importeTotal", VoucherKind.CREDIT_NOTE: "valorModificacion"}


def _decimal(element: etree._Element, path: str) -> Decimal:
    return Decimal(_text(element, path) or "0")


def _date(written: str) -> date:
    return datetime.strptime(written, DATE_FORMAT).date()


def _buyer(info: etree._Element, extra: dict[str, str]) -> Buyer:
    kinds = {code: kind for kind, code in BUYER_CODES.items()}
    kind = kinds.get(_text(info, "tipoIdentificacionComprador"), BuyerKind.FINAL_CONSUMER)
    named = kind != BuyerKind.FINAL_CONSUMER
    return Buyer(
        kind=kind,
        identification=_text(info, "identificacionComprador") if named else None,
        name=_text(info, "razonSocialComprador") if named else None,
        email=extra.get(EMAIL_FIELD),
    )


def _lines(root: etree._Element, kind: VoucherKind) -> tuple[Line, ...]:
    return tuple(
        Line(
            code=_text(detail, CODE_TAGS[kind]),
            description=_text(detail, "descripcion"),
            quantity=_decimal(detail, "cantidad"),
            unit_price=_decimal(detail, "precioUnitario"),
            discount=_decimal(detail, "descuento"),
            subtotal=_decimal(detail, "precioTotalSinImpuesto"),
            vat=_decimal(detail, "impuestos/impuesto/valor"),
            rate_code=_text(detail, "impuestos/impuesto/codigoPorcentaje"),
            rate_percentage=_decimal(detail, "impuestos/impuesto/tarifa"),
        )
        for detail in root.iterfind("detalles/detalle")
    )


def _tax(info: etree._Element, code: str) -> Decimal:
    return sum(
        (
            _decimal(tax, "valor")
            for tax in info.iterfind("totalConImpuestos/totalImpuesto")
            if _text(tax, "codigo") == code
        ),
        ZERO,
    )


def _payments(info: etree._Element) -> tuple[Payment, ...]:
    return tuple(
        Payment(form=_text(paid, "formaPago"), amount=_decimal(paid, "total"))
        for paid in info.iterfind("pagos/pago")
    )


def _parts(
    root: etree._Element,
) -> tuple[VoucherKind, etree._Element, etree._Element, dict[str, str]]:
    kind = next((name for name, tag in ROOTS.items() if tag == root.tag), None)
    tributaria = root.find("infoTributaria")
    info = root.find(INFO[kind]) if kind else None
    if kind is None or tributaria is None or info is None:
        raise VoucherInvalid("The document holds no invoice or credit note")
    extra = {
        field.get("nombre", ""): (field.text or "")
        for field in root.iterfind("infoAdicional/campoAdicional")
    }
    return kind, tributaria, info, extra


def _voucher(
    root: etree._Element,
    kind: VoucherKind,
    tributaria: etree._Element,
    info: etree._Element,
    extra: dict[str, str],
) -> Voucher:
    environments = {code: name for name, code in ENVIRONMENT_CODES.items()}
    modified = (
        Modified(
            number=_text(info, "numDocModificado"),
            issued_on=_date(_text(info, "fechaEmisionDocSustento")),
            reason=_text(info, "motivo"),
        )
        if kind == VoucherKind.CREDIT_NOTE
        else None
    )
    return Voucher(
        kind=kind,
        environment=environments.get(_text(tributaria, "ambiente"), Environment.TEST),
        access_key=_text(tributaria, "claveAcceso"),
        establishment=_text(tributaria, "estab"),
        point=_text(tributaria, "ptoEmi"),
        sequential=int(_text(tributaria, "secuencial") or "0"),
        establishment_address=_text(info, "dirEstablecimiento"),
        issued_on=_date(_text(info, "fechaEmision")),
        buyer=_buyer(info, extra),
        lines=_lines(root, kind),
        payments=_payments(info),
        modified=modified,
        extra=tuple(
            (name, value)
            for name, value in extra.items()
            if name not in (EMAIL_FIELD, SYSTEM_PROVIDER_FIELD)
        ),
    )


def read_voucher(document: bytes) -> Voucher:
    """The voucher a signed or unsigned document carries."""
    root = etree.fromstring(document, PARSER)
    return _voucher(root, *_parts(root))


def read(record: str) -> Issued:
    held = etree.fromstring(record.encode(), PARSER)
    authorized_at = _text(held, "fechaAutorizacion")
    if not authorized_at:
        raise VoucherInvalid("The record holds no authorized invoice or credit note")
    root = etree.fromstring(_text(held, "comprobante").encode(), PARSER)
    kind, tributaria, info, extra = _parts(root)
    voucher = _voucher(root, kind, tributaria, info, extra)
    issuer = Issuer(
        legal_name=_text(tributaria, "razonSocial"),
        ruc=_text(tributaria, "ruc"),
        head_office_address=_text(tributaria, "dirMatriz"),
        keeps_accounts=_text(info, "obligadoContabilidad") == YES,
        trade_name=_text(tributaria, "nombreComercial") or None,
        special_taxpayer=_text(info, "contribuyenteEspecial") or None,
        withholding_agent=_text(tributaria, "agenteRetencion") or None,
        rimpe=bool(_text(tributaria, RIMPE_TYPE)),
        system_provider_ruc=extra.get(SYSTEM_PROVIDER_FIELD),
    )
    return Issued(
        voucher=voucher,
        issuer=issuer,
        authorized_at=datetime.fromisoformat(authorized_at),
        authorization_number=_text(held, "numeroAutorizacion"),
        total=_decimal(info, TOTAL_TAGS[kind]),
        ice=_tax(info, ICE_TAX),
        irbpnr=_tax(info, IRBPNR_TAX),
        tip=_decimal(info, "propina") if kind == VoucherKind.INVOICE else None,
    )
