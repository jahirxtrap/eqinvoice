from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import cache

from lxml import etree

from eqinvoice.errors import VoucherInvalid
from eqinvoice.model import (
    ZERO,
    AnyVoucher,
    Buyer,
    BuyerKind,
    Issuer,
    Payment,
    Reference,
    RemissionGuide,
    Voucher,
    VoucherKind,
    Withholding,
    sequential_code,
)
from eqinvoice.protocol import (
    ABROAD_PAYMENT,
    ADDRESSED,
    BUYER_CODES,
    CODE_TAGS,
    CURRENCY_NAME,
    DATE_FORMAT,
    DOCUMENT_CODES,
    EMAIL_FIELD,
    ENVIRONMENT_CODES,
    FISCAL_PERIOD_FORMAT,
    INFO,
    LOCAL_PAYMENT,
    MOST_EXTRA_FIELDS,
    NO,
    NORMAL_EMISSION,
    NOTES,
    PARSER,
    PARTIES,
    POPULAR_BUSINESS_LEGEND,
    PRICED,
    RIMPE_TYPE,
    ROOTS,
    SCHEMA_DIR,
    SCHEMAS,
    SYSTEM_PROVIDER_FIELD,
    VAT_TAX,
    VERSIONS,
    VOUCHER_ID,
    XSD_NS,
    YES,
    buyer_shown,
)


def money(amount: Decimal) -> str:
    return f"{amount:.2f}"


def _six(amount: Decimal) -> str:
    return f"{amount:.6f}"


def _percentage(amount: Decimal) -> str:
    return f"{amount.normalize():f}"


def _day(on: date) -> str:
    return on.strftime(DATE_FORMAT)


def _yes(flag: bool) -> str:
    return YES if flag else NO


def _add(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    child = etree.SubElement(parent, tag)
    if text is not None:
        child.text = text
    return child


def _optional(parent: etree._Element, tag: str, text: str | None) -> None:
    if text:
        _add(parent, tag, text)


def _info_tributaria(root: etree._Element, voucher: AnyVoucher, issuer: Issuer) -> None:
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
    _add(info, "secuencial", sequential_code(voucher.sequential))
    _add(info, "dirMatriz", issuer.head_office_address)
    _optional(info, "agenteRetencion", issuer.withholding_agent)
    _optional(info, RIMPE_TYPE, issuer_legend(issuer))


def _named(party: Buyer, kind: VoucherKind) -> tuple[str, str]:
    if party.kind == BuyerKind.FINAL_CONSUMER and kind != VoucherKind.INVOICE:
        raise VoucherInvalid(f"Only an invoice goes to a final consumer, not a {kind}")
    return buyer_shown(party.kind, party.name, party.identification)


def _party(info: etree._Element, voucher: Voucher) -> None:
    party, suffix = voucher.buyer, PARTIES[voucher.kind]
    name, identification = _named(party, voucher.kind)
    _add(info, f"tipoIdentificacion{suffix}", BUYER_CODES[party.kind])
    _add(info, f"razonSocial{suffix}", name)
    _add(info, f"identificacion{suffix}", identification)
    if voucher.kind in ADDRESSED:
        _optional(info, f"direccion{suffix}", party.address)


def _issued(info: etree._Element, voucher: AnyVoucher) -> None:
    _add(info, "fechaEmision", _day(voucher.issued_on))
    _add(info, "dirEstablecimiento", voucher.establishment_address)


def _duties(info: etree._Element, issuer: Issuer) -> None:
    _optional(info, "contribuyenteEspecial", issuer.special_taxpayer)
    _add(info, "obligadoContabilidad", _yes(issuer.keeps_accounts))


def _tax(
    parent: etree._Element,
    tag: str,
    code: str,
    base: Decimal,
    vat: Decimal,
    rate: Decimal | None = None,
) -> None:
    tax = _add(parent, tag)
    _add(tax, "codigo", VAT_TAX)
    _add(tax, "codigoPorcentaje", code)
    if rate is not None:
        _add(tax, "tarifa", money(rate))
    _add(tax, "baseImponible", money(base))
    _add(tax, "valor", money(vat))


def _totals(info: etree._Element, voucher: Voucher) -> None:
    totals = _add(info, "totalConImpuestos")
    for group in voucher.tax_groups:
        _tax(totals, "totalImpuesto", group.code, group.base, group.vat)


def _payments(parent: etree._Element, payments: tuple[Payment, ...]) -> None:
    if not payments:
        return
    held = _add(parent, "pagos")
    for payment in payments:
        paid = _add(held, "pago")
        _add(paid, "formaPago", payment.form)
        _add(paid, "total", money(payment.amount))


def _sale_info(root: etree._Element, voucher: Voucher, issuer: Issuer) -> None:
    info = _add(root, INFO[voucher.kind])
    _issued(info, voucher)
    _duties(info, issuer)
    _party(info, voucher)
    _add(info, "totalSinImpuestos", money(voucher.subtotal))
    _add(info, "totalDescuento", money(voucher.discount))
    _totals(info, voucher)
    if voucher.kind == VoucherKind.INVOICE:
        _add(info, "propina", money(ZERO))
    _add(info, "importeTotal", money(voucher.total))
    _add(info, "moneda", CURRENCY_NAME)
    _payments(info, voucher.payment_forms)


def _note_info(root: etree._Element, voucher: Voucher, issuer: Issuer) -> None:
    modified = voucher.modified
    if modified is None:
        raise VoucherInvalid(f"A {voucher.kind} names the invoice it modifies")
    info = _add(root, INFO[voucher.kind])
    _issued(info, voucher)
    _party(info, voucher)
    _duties(info, issuer)
    _add(info, "codDocModificado", DOCUMENT_CODES[VoucherKind.INVOICE])
    _add(info, "numDocModificado", modified.number)
    _add(info, "fechaEmisionDocSustento", _day(modified.issued_on))
    _add(info, "totalSinImpuestos", money(voucher.subtotal))
    if voucher.kind == VoucherKind.CREDIT_NOTE:
        _add(info, "valorModificacion", money(voucher.total))
        _add(info, "moneda", CURRENCY_NAME)
        _totals(info, voucher)
        _add(info, "motivo", modified.reason)
        return
    taxes = _add(info, "impuestos")
    for group in voucher.tax_groups:
        _tax(taxes, "impuesto", group.code, group.base, group.vat, group.rate)
    _add(info, "valorTotal", money(voucher.total))
    _payments(info, voucher.payment_forms)


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
        taxes = _add(detail, "impuestos")
        _tax(taxes, "impuesto", line.rate_code, line.subtotal, line.vat, line.rate_percentage)


def _charges(root: etree._Element, voucher: Voucher) -> None:
    reasons = _add(root, "motivos")
    for line in voucher.lines:
        reason = _add(reasons, "motivo")
        _add(reason, "razon", line.description)
        _add(reason, "valor", money(line.subtotal))


def _supporting(parent: etree._Element, reference: Reference | None) -> None:
    if reference is None:
        return
    _add(parent, "codDocSustento", reference.document_code)
    _add(parent, "numDocSustento", reference.number)
    _optional(parent, "numAutDocSustento", reference.authorization)
    _add(parent, "fechaEmisionDocSustento", _day(reference.issued_on))


def _guide(root: etree._Element, guide: RemissionGuide, issuer: Issuer) -> None:
    info = _add(root, INFO[guide.kind])
    _optional(info, "dirEstablecimiento", guide.establishment_address)
    _add(info, "dirPartida", guide.departure)
    name, identification = _named(guide.carrier, guide.kind)
    _add(info, "razonSocialTransportista", name)
    _add(info, "tipoIdentificacionTransportista", BUYER_CODES[guide.carrier.kind])
    _add(info, "rucTransportista", identification)
    _add(info, "obligadoContabilidad", _yes(issuer.keeps_accounts))
    _optional(info, "contribuyenteEspecial", issuer.special_taxpayer)
    _add(info, "fechaIniTransporte", _day(guide.starts_on))
    _add(info, "fechaFinTransporte", _day(guide.ends_on))
    _add(info, "placa", guide.plate)
    recipients = _add(root, "destinatarios")
    for recipient in guide.recipients:
        held = _add(recipients, "destinatario")
        _add(held, "identificacionDestinatario", recipient.identification)
        _add(held, "razonSocialDestinatario", recipient.name)
        _add(held, "dirDestinatario", recipient.address)
        _add(held, "motivoTraslado", recipient.reason)
        _optional(held, "docAduaneroUnico", recipient.customs_document)
        _optional(held, "codEstabDestino", recipient.destination)
        _optional(held, "ruta", recipient.route)
        _supporting(held, recipient.supporting)
        items = _add(held, "detalles")
        for item in recipient.items:
            detail = _add(items, "detalle")
            _optional(detail, "codigoInterno", item.code)
            _optional(detail, "codigoAdicional", item.extra_code)
            _add(detail, "descripcion", item.description)
            _add(detail, "cantidad", _six(item.quantity))
            if item.details:
                extras = _add(detail, "detallesAdicionales")
                for name, value in item.details:
                    etree.SubElement(extras, "detAdicional", nombre=name, valor=value)


def _withholding(root: etree._Element, held: Withholding, issuer: Issuer) -> None:
    info = _add(root, INFO[held.kind])
    _issued(info, held)
    _duties(info, issuer)
    name, identification = _named(held.subject, held.kind)
    _add(info, "tipoIdentificacionSujetoRetenido", BUYER_CODES[held.subject.kind])
    _optional(info, "tipoSujetoRetenido", held.subject_type)
    _add(info, "parteRel", _yes(held.related_party))
    _add(info, "razonSocialSujetoRetenido", name)
    _add(info, "identificacionSujetoRetenido", identification)
    _add(info, "periodoFiscal", held.fiscal_period.strftime(FISCAL_PERIOD_FORMAT))
    documents = _add(root, "docsSustento")
    for document in held.documents:
        support = _add(documents, "docSustento")
        reference = document.document
        _add(support, "codSustento", document.support_code)
        _add(support, "codDocSustento", reference.document_code)
        _add(support, "numDocSustento", reference.number.replace("-", ""))
        _add(support, "fechaEmisionDocSustento", _day(reference.issued_on))
        if document.registered_on is not None:
            _add(support, "fechaRegistroContable", _day(document.registered_on))
        _optional(support, "numAutDocSustento", reference.authorization)
        abroad = document.abroad
        _add(support, "pagoLocExt", LOCAL_PAYMENT if abroad is None else ABROAD_PAYMENT)
        if abroad is not None:
            _add(support, "tipoRegi", abroad.regime)
            _add(support, "paisEfecPago", abroad.country)
            _add(support, "aplicConvDobTrib", _yes(abroad.double_taxation))
            _add(support, "pagExtSujRetNorLeg", _yes(abroad.withheld_by_law))
            _add(support, "pagoRegFis", _yes(abroad.preferential))
        _add(support, "totalSinImpuestos", money(document.subtotal))
        _add(support, "importeTotal", money(document.total))
        taxes = _add(support, "impuestosDocSustento")
        for group in document.taxes:
            tax = _add(taxes, "impuestoDocSustento")
            _add(tax, "codImpuestoDocSustento", VAT_TAX)
            _add(tax, "codigoPorcentaje", group.code)
            _add(tax, "baseImponible", money(group.base))
            _add(tax, "tarifa", money(group.rate))
            _add(tax, "valorImpuesto", money(group.vat))
        retentions = _add(support, "retenciones")
        for retention in document.retentions:
            withheld = _add(retentions, "retencion")
            _add(withheld, "codigo", retention.tax)
            _add(withheld, "codigoRetencion", retention.code)
            _add(withheld, "baseImponible", money(retention.base))
            _add(withheld, "porcentajeRetener", _percentage(retention.percentage))
            _add(withheld, "valorRetenido", money(retention.withheld))
        _payments(support, document.payment_forms)


def _extra(root: etree._Element, voucher: AnyVoucher, issuer: Issuer) -> None:
    fields = list(voucher.extra)
    if voucher.party.email:
        fields.insert(0, (EMAIL_FIELD, voucher.party.email))
    if issuer.system_provider_ruc:
        fields.append((SYSTEM_PROVIDER_FIELD, issuer.system_provider_ruc))
    if not fields:
        return
    extra = _add(root, "infoAdicional")
    for name, value in fields[:MOST_EXTRA_FIELDS]:
        _add(extra, "campoAdicional", value).set("nombre", name)


def build(voucher: AnyVoucher, issuer: Issuer) -> etree._Element:
    root = etree.Element(ROOTS[voucher.kind], id=VOUCHER_ID, version=VERSIONS[voucher.kind])
    _info_tributaria(root, voucher, issuer)
    if isinstance(voucher, RemissionGuide) and voucher.kind == VoucherKind.REMISSION_GUIDE:
        _guide(root, voucher, issuer)
    elif isinstance(voucher, Withholding) and voucher.kind == VoucherKind.WITHHOLDING:
        _withholding(root, voucher, issuer)
    elif isinstance(voucher, Voucher) and voucher.kind in NOTES:
        _note_info(root, voucher, issuer)
        (_details if voucher.kind == VoucherKind.CREDIT_NOTE else _charges)(root, voucher)
    elif isinstance(voucher, Voucher) and voucher.kind in PRICED:
        _sale_info(root, voucher, issuer)
        _details(root, voucher)
    else:
        raise VoucherInvalid(f"A {type(voucher).__name__} cannot be a {voucher.kind}")
    _extra(root, voucher, issuer)
    return root


@cache
def _schema_document(kind: VoucherKind) -> etree._ElementTree:
    return etree.parse(str(SCHEMA_DIR / SCHEMAS[kind]), PARSER)


def _legend_rule(document: etree._ElementTree) -> etree._Element | None:
    return document.find(f"{{{XSD_NS}}}simpleType[@name='{RIMPE_TYPE}']/{{{XSD_NS}}}restriction")


@cache
def _schema(kind: VoucherKind) -> etree.XMLSchema:
    """The SRI's schema, also admitting the popular business legend its ficha técnica requires."""
    document = _schema_document(kind)
    rule = _legend_rule(document)
    if rule is not None:
        etree.SubElement(rule, f"{{{XSD_NS}}}pattern", value=POPULAR_BUSINESS_LEGEND)
    return etree.XMLSchema(document)


@cache
def rimpe_legend() -> str:
    rule = _legend_rule(_schema_document(VoucherKind.INVOICE))
    pattern = None if rule is None else rule.find(f"{{{XSD_NS}}}pattern")
    if pattern is None:
        raise VoucherInvalid("The SRI's schema no longer fixes the RIMPE legend")
    return str(pattern.get("value"))


def issuer_legend(issuer: Issuer) -> str | None:
    if issuer.popular_business:
        return POPULAR_BUSINESS_LEGEND
    return rimpe_legend() if issuer.rimpe else None


def validate(kind: VoucherKind, document: bytes) -> None:
    schema = _schema(kind)
    if not schema.validate(etree.fromstring(document, PARSER)):
        raise VoucherInvalid("; ".join(str(error.message) for error in schema.error_log))
