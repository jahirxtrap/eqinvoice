from __future__ import annotations

from decimal import Decimal
from functools import cache

from lxml import etree

from eqinvoice.errors import VoucherInvalid
from eqinvoice.model import ZERO, Issuer, Voucher, VoucherKind
from eqinvoice.protocol import (
    BUYER_CODES,
    CODE_TAGS,
    CURRENCY_NAME,
    DATE_FORMAT,
    DOCUMENT_CODES,
    EMAIL_FIELD,
    ENVIRONMENT_CODES,
    INFO,
    MOST_EXTRA_FIELDS,
    NO,
    NORMAL_EMISSION,
    PARSER,
    RIMPE_TYPE,
    ROOTS,
    SCHEMA_DIR,
    SCHEMAS,
    SYSTEM_PROVIDER_FIELD,
    VAT_TAX,
    VERSION,
    VOUCHER_ID,
    XSD_NS,
    YES,
    buyer_shown,
)


def money(amount: Decimal) -> str:
    return f"{amount:.2f}"


def _six(amount: Decimal) -> str:
    return f"{amount:.6f}"


def _add(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    child = etree.SubElement(parent, tag)
    if text is not None:
        child.text = text
    return child


def _optional(parent: etree._Element, tag: str, text: str | None) -> None:
    if text:
        _add(parent, tag, text)


def _info_tributaria(root: etree._Element, voucher: Voucher, issuer: Issuer) -> None:
    info = _add(root, "infoTributaria")
    _add(info, "ambiente", ENVIRONMENT_CODES[voucher.environment])
    _add(info, "tipoEmision", NORMAL_EMISSION)
    _add(info, "razonSocial", issuer.legal_name)
    _optional(info, "nombreComercial", issuer.trade_name)
    _add(info, "ruc", issuer.ruc)
    _add(info, "claveAcceso", voucher.access_key)
    _add(info, "codDoc", DOCUMENT_CODES[voucher.kind])
    _add(info, "estab", voucher.establishment)
    _add(info, "ptoEmi", voucher.point)
    _add(info, "secuencial", f"{voucher.sequential:09d}")
    _add(info, "dirMatriz", issuer.head_office_address)
    _optional(info, "agenteRetencion", issuer.withholding_agent)
    if issuer.rimpe:
        _add(info, RIMPE_TYPE, rimpe_legend())


def _buyer(info: etree._Element, voucher: Voucher) -> None:
    buyer = voucher.buyer
    name, identification = buyer_shown(buyer.kind, buyer.name, buyer.identification)
    _add(info, "tipoIdentificacionComprador", BUYER_CODES[buyer.kind])
    _add(info, "razonSocialComprador", name)
    _add(info, "identificacionComprador", identification)


def _taxes(info: etree._Element, voucher: Voucher) -> None:
    totals = _add(info, "totalConImpuestos")
    for group in voucher.tax_groups:
        tax = _add(totals, "totalImpuesto")
        _add(tax, "codigo", VAT_TAX)
        _add(tax, "codigoPorcentaje", group.code)
        _add(tax, "baseImponible", money(group.base))
        _add(tax, "valor", money(group.vat))


def _accounts(info: etree._Element, issuer: Issuer) -> None:
    _add(info, "obligadoContabilidad", YES if issuer.keeps_accounts else NO)


def _invoice_info(root: etree._Element, voucher: Voucher, issuer: Issuer) -> None:
    info = _add(root, INFO[VoucherKind.INVOICE])
    _add(info, "fechaEmision", voucher.issued_on.strftime(DATE_FORMAT))
    _add(info, "dirEstablecimiento", voucher.establishment_address)
    _optional(info, "contribuyenteEspecial", issuer.special_taxpayer)
    _accounts(info, issuer)
    _buyer(info, voucher)
    _add(info, "totalSinImpuestos", money(voucher.subtotal))
    _add(info, "totalDescuento", money(voucher.discount))
    _taxes(info, voucher)
    _add(info, "propina", money(ZERO))
    _add(info, "importeTotal", money(voucher.total))
    _add(info, "moneda", CURRENCY_NAME)
    payments = _add(info, "pagos")
    for payment in voucher.payment_forms:
        paid = _add(payments, "pago")
        _add(paid, "formaPago", payment.form)
        _add(paid, "total", money(payment.amount))


def _credit_info(root: etree._Element, voucher: Voucher, issuer: Issuer) -> None:
    modified = voucher.modified
    if modified is None:
        raise VoucherInvalid("A credit note names the invoice it modifies")
    info = _add(root, INFO[VoucherKind.CREDIT_NOTE])
    _add(info, "fechaEmision", voucher.issued_on.strftime(DATE_FORMAT))
    _add(info, "dirEstablecimiento", voucher.establishment_address)
    _buyer(info, voucher)
    _optional(info, "contribuyenteEspecial", issuer.special_taxpayer)
    _accounts(info, issuer)
    _add(info, "codDocModificado", DOCUMENT_CODES[VoucherKind.INVOICE])
    _add(info, "numDocModificado", modified.number)
    _add(info, "fechaEmisionDocSustento", modified.issued_on.strftime(DATE_FORMAT))
    _add(info, "totalSinImpuestos", money(voucher.subtotal))
    _add(info, "valorModificacion", money(voucher.total))
    _add(info, "moneda", CURRENCY_NAME)
    _taxes(info, voucher)
    _add(info, "motivo", modified.reason)


def _details(root: etree._Element, voucher: Voucher) -> None:
    details = _add(root, "detalles")
    for line in voucher.lines:
        detail = _add(details, "detalle")
        _add(detail, CODE_TAGS[voucher.kind], line.code)
        _add(detail, "descripcion", line.description)
        _add(detail, "cantidad", _six(line.quantity))
        _add(detail, "precioUnitario", _six(line.unit_price))
        _add(detail, "descuento", money(line.discount))
        _add(detail, "precioTotalSinImpuesto", money(line.subtotal))
        tax = _add(_add(detail, "impuestos"), "impuesto")
        _add(tax, "codigo", VAT_TAX)
        _add(tax, "codigoPorcentaje", line.rate_code)
        _add(tax, "tarifa", money(line.rate_percentage))
        _add(tax, "baseImponible", money(line.subtotal))
        _add(tax, "valor", money(line.vat))


def _extra(root: etree._Element, voucher: Voucher, issuer: Issuer) -> None:
    fields = list(voucher.extra)
    if voucher.buyer.email:
        fields.insert(0, (EMAIL_FIELD, voucher.buyer.email))
    if issuer.system_provider_ruc:
        fields.append((SYSTEM_PROVIDER_FIELD, issuer.system_provider_ruc))
    if not fields:
        return
    extra = _add(root, "infoAdicional")
    for name, value in fields[:MOST_EXTRA_FIELDS]:
        _add(extra, "campoAdicional", value).set("nombre", name)


def build(voucher: Voucher, issuer: Issuer) -> etree._Element:
    root = etree.Element(ROOTS[voucher.kind], id=VOUCHER_ID, version=VERSION)
    _info_tributaria(root, voucher, issuer)
    if voucher.kind == VoucherKind.INVOICE:
        _invoice_info(root, voucher, issuer)
    else:
        _credit_info(root, voucher, issuer)
    _details(root, voucher)
    _extra(root, voucher, issuer)
    return root


@cache
def _schema_document(kind: VoucherKind) -> etree._ElementTree:
    return etree.parse(str(SCHEMA_DIR / SCHEMAS[kind]), PARSER)


@cache
def _schema(kind: VoucherKind) -> etree.XMLSchema:
    return etree.XMLSchema(_schema_document(kind))


@cache
def rimpe_legend() -> str:
    pattern = _schema_document(VoucherKind.INVOICE).find(
        f"{{{XSD_NS}}}simpleType[@name='{RIMPE_TYPE}']/{{{XSD_NS}}}restriction/{{{XSD_NS}}}pattern"
    )
    if pattern is None:
        raise VoucherInvalid("The SRI's schema no longer fixes the RIMPE legend")
    return str(pattern.get("value"))


def validate(kind: VoucherKind, document: bytes) -> None:
    schema = _schema(kind)
    if not schema.validate(etree.fromstring(document, PARSER)):
        raise VoucherInvalid("; ".join(str(error.message) for error in schema.error_log))
