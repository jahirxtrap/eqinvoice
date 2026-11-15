from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeVar

from lxml import etree

from eqinvoice.errors import VoucherInvalid
from eqinvoice.model import (
    ZERO,
    Abroad,
    AnyVoucher,
    Buyer,
    BuyerKind,
    Environment,
    GuideItem,
    Issued,
    Issuer,
    Line,
    Modified,
    Payment,
    PaymentForm,
    Recipient,
    Reference,
    RemissionGuide,
    Retention,
    RetentionTax,
    SubjectType,
    SupportDocument,
    TaxGroup,
    Voucher,
    VoucherKind,
    Withholding,
)
from eqinvoice.pricing import cents
from eqinvoice.protocol import (
    ABROAD_PAYMENT,
    BUYER_CODES,
    CODE_TAGS,
    DATE_FORMAT,
    EMAIL_FIELD,
    ENVIRONMENT_CODES,
    FISCAL_PERIOD_FORMAT,
    ICE_TAX,
    INFO,
    IRBPNR_TAX,
    NOTES,
    PARSER,
    PARTIES,
    POPULAR_BUSINESS_LEGEND,
    RIMPE_TYPE,
    ROOTS,
    SYSTEM_PROVIDER_FIELD,
    TAX_TOTALS,
    TOTAL_TAGS,
    YES,
    ZERO_VAT_RATE,
)
from eqinvoice.protocol import text_at as _text

Listed = TypeVar("Listed", bound=StrEnum)
ACCESS_KEY_DATE = "%d%m%Y"


def _decimal(element: etree._Element, path: str) -> Decimal:
    return Decimal(_text(element, path) or "0")


def _date(written: str) -> date:
    return datetime.strptime(written, DATE_FORMAT).date()


def _listed(table: type[Listed], code: str) -> Listed:
    try:
        return table(code)
    except ValueError:
        raise VoucherInvalid(f"{code} is no {table.__name__} of the SRI's table") from None


def _party(
    info: etree._Element, suffix: str, email: str | None, identity: str = "identificacion"
) -> Buyer:
    kinds = {code: kind for kind, code in BUYER_CODES.items()}
    kind = kinds.get(_text(info, f"tipoIdentificacion{suffix}"), BuyerKind.FINAL_CONSUMER)
    named = kind != BuyerKind.FINAL_CONSUMER
    return Buyer(
        kind=kind,
        identification=_text(info, f"{identity}{suffix}") if named else None,
        name=_text(info, f"razonSocial{suffix}") if named else None,
        email=email,
        address=_text(info, f"direccion{suffix}") or None,
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


def _assigned(amounts: list[Decimal], bases: list[Decimal]) -> list[int | None] | None:
    """Each amount's base, filling every base exactly; None for one that no base takes."""
    left, owners = list(bases), list[int | None]()
    ahead = [sum(amounts[at:], ZERO) for at in range(len(amounts) + 1)]

    def place(at: int) -> bool:
        if at == len(amounts) or ahead[at] < sum(left, ZERO):
            return at == len(amounts) and not any(left)
        fitting = [index for index, rest in enumerate(left) if amounts[at] <= rest]
        for index in (*fitting, None):
            if index is not None:
                left[index] -= amounts[at]
            owners.append(index)
            if place(at + 1):
                return True
            owners.pop()
            if index is not None:
                left[index] += amounts[at]
        return False

    return owners if place(0) else None


def _spread(total: Decimal, amounts: list[Decimal]) -> list[Decimal]:
    whole = sum(amounts, ZERO)
    shares = [cents(total * amount / whole) if whole else ZERO for amount in amounts[:-1]]
    return [*shares, total - sum(shares, ZERO)]


def _charges(root: etree._Element, info: etree._Element) -> tuple[Line, ...]:
    """A debit note's reasons as lines: each takes the rate whose base it fills, else none."""
    reasons = [
        (_text(held, "razon"), _decimal(held, "valor")) for held in root.iterfind("motivos/motivo")
    ]
    groups = [
        TaxGroup(
            _text(tax, "codigoPorcentaje"),
            _decimal(tax, "baseImponible"),
            _decimal(tax, "valor"),
            _decimal(tax, "tarifa"),
        )
        for tax in info.iterfind("impuestos/impuesto")
    ]
    owners = _assigned([value for _, value in reasons], [group.base for group in groups])
    if owners is None:
        raise VoucherInvalid("The debit note's reasons do not add up to its taxed bases")
    untaxed = TaxGroup(ZERO_VAT_RATE, ZERO, ZERO, ZERO)
    vats: dict[int, Decimal] = {}
    for index, group in enumerate(groups):
        mine = [at for at, owner in enumerate(owners) if owner == index]
        if mine:
            vats.update(zip(mine, _spread(group.vat, [reasons[at][1] for at in mine]), strict=True))
    owned = [untaxed if owner is None else groups[owner] for owner in owners]
    return tuple(
        Line(
            code="",
            description=reason,
            quantity=Decimal(1),
            unit_price=value,
            discount=ZERO,
            subtotal=value,
            vat=vats.get(at, ZERO),
            rate_code=owned[at].code,
            rate_percentage=owned[at].rate,
        )
        for at, (reason, value) in enumerate(reasons)
    )


def _tax(info: etree._Element, kind: VoucherKind, code: str) -> Decimal:
    path = TAX_TOTALS.get(kind)
    if path is None:
        return ZERO
    return sum(
        (_decimal(tax, "valor") for tax in info.iterfind(path) if _text(tax, "codigo") == code),
        ZERO,
    )


def _payments(parent: etree._Element) -> tuple[Payment, ...]:
    return tuple(
        Payment(form=_listed(PaymentForm, _text(paid, "formaPago")), amount=_decimal(paid, "total"))
        for paid in parent.iterfind("pagos/pago")
    )


def _dashed(number: str) -> str:
    return number if "-" in number else f"{number[:3]}-{number[3:6]}-{number[6:]}"


def _reference(element: etree._Element) -> Reference | None:
    code = _text(element, "codDocSustento")
    if not code:
        return None
    return Reference(
        document_code=code,
        number=_dashed(_text(element, "numDocSustento")),
        issued_on=_date(_text(element, "fechaEmisionDocSustento")),
        authorization=_text(element, "numAutDocSustento") or None,
    )


def _parts(
    root: etree._Element,
) -> tuple[VoucherKind, etree._Element, etree._Element, dict[str, str]]:
    kind = next((name for name, tag in ROOTS.items() if tag == root.tag), None)
    tributaria = root.find("infoTributaria")
    info = root.find(INFO[kind]) if kind else None
    if kind is None or tributaria is None or info is None:
        raise VoucherInvalid("The document holds no voucher of the SRI's")
    extra = {
        field.get("nombre", ""): (field.text or "")
        for field in root.iterfind("infoAdicional/campoAdicional")
    }
    return kind, tributaria, info, extra


def _numbered(kind: VoucherKind, tributaria: etree._Element, info: etree._Element) -> dict:
    environments = {code: name for name, code in ENVIRONMENT_CODES.items()}
    access_key = _text(tributaria, "claveAcceso")
    written = _text(info, "fechaEmision")
    return {
        "kind": kind,
        "environment": environments.get(_text(tributaria, "ambiente"), Environment.TEST),
        "access_key": access_key,
        "establishment": _text(tributaria, "estab"),
        "point": _text(tributaria, "ptoEmi"),
        "sequential": int(_text(tributaria, "secuencial") or "0"),
        "establishment_address": _text(info, "dirEstablecimiento"),
        "issued_on": _date(written)
        if written
        else datetime.strptime(access_key[:8], ACCESS_KEY_DATE).date(),
    }


def _priced(
    root: etree._Element, kind: VoucherKind, info: etree._Element, email: str | None
) -> dict:
    modified = (
        Modified(
            number=_text(info, "numDocModificado"),
            issued_on=_date(_text(info, "fechaEmisionDocSustento")),
            reason=_text(info, "motivo"),
        )
        if kind in NOTES
        else None
    )
    return {
        "buyer": _party(info, PARTIES[kind], email),
        "lines": _charges(root, info) if kind == VoucherKind.DEBIT_NOTE else _lines(root, kind),
        "payments": _payments(info),
        "modified": modified,
    }


def _guide(root: etree._Element, info: etree._Element, email: str | None) -> dict:
    return {
        "carrier": _party(info, "Transportista", email, identity="ruc"),
        "plate": _text(info, "placa"),
        "departure": _text(info, "dirPartida"),
        "starts_on": _date(_text(info, "fechaIniTransporte")),
        "ends_on": _date(_text(info, "fechaFinTransporte")),
        "recipients": tuple(
            Recipient(
                identification=_text(held, "identificacionDestinatario"),
                name=_text(held, "razonSocialDestinatario"),
                address=_text(held, "dirDestinatario"),
                reason=_text(held, "motivoTraslado"),
                items=tuple(
                    GuideItem(
                        description=_text(detail, "descripcion"),
                        quantity=_decimal(detail, "cantidad"),
                        code=_text(detail, "codigoInterno") or None,
                        extra_code=_text(detail, "codigoAdicional") or None,
                        details=tuple(
                            (extra.get("nombre", ""), extra.get("valor", ""))
                            for extra in detail.iterfind("detallesAdicionales/detAdicional")
                        ),
                    )
                    for detail in held.iterfind("detalles/detalle")
                ),
                route=_text(held, "ruta") or None,
                destination=_text(held, "codEstabDestino") or None,
                customs_document=_text(held, "docAduaneroUnico") or None,
                supporting=_reference(held),
            )
            for held in root.iterfind("destinatarios/destinatario")
        ),
    }


def _abroad(support: etree._Element) -> Abroad | None:
    if _text(support, "pagoLocExt") != ABROAD_PAYMENT:
        return None
    return Abroad(
        regime=_text(support, "tipoRegi"),
        country=_text(support, "paisEfecPago"),
        double_taxation=_text(support, "aplicConvDobTrib") == YES,
        withheld_by_law=_text(support, "pagExtSujRetNorLeg") == YES,
        preferential=_text(support, "pagoRegFis") == YES,
    )


def _retention(held: etree._Element) -> Retention:
    return Retention(
        tax=_listed(RetentionTax, _text(held, "codigo")),
        code=_text(held, "codigoRetencion"),
        base=_decimal(held, "baseImponible"),
        percentage=_decimal(held, "porcentajeRetener"),
        withheld=_decimal(held, "valorRetenido"),
    )


def _named_document(element: etree._Element) -> Reference:
    reference = _reference(element)
    if reference is None:
        raise VoucherInvalid("A withholding's support document names no document")
    return reference


def _legacy_documents(root: etree._Element) -> tuple[SupportDocument, ...]:
    """A 1.0.0 withholding's retentions, gathered by the document each one names."""
    gathered: dict[Reference, list[Retention]] = {}
    for held in root.iterfind("impuestos/impuesto"):
        gathered.setdefault(_named_document(held), []).append(_retention(held))
    return tuple(
        SupportDocument(
            support_code="",
            document=reference,
            taxes=(),
            retentions=tuple(retentions),
            payments=(),
        )
        for reference, retentions in gathered.items()
    )


def _support(support: etree._Element) -> SupportDocument:
    reference = _named_document(support)
    registered = _text(support, "fechaRegistroContable")
    return SupportDocument(
        support_code=_text(support, "codSustento"),
        document=reference,
        taxes=tuple(
            TaxGroup(
                _text(tax, "codigoPorcentaje"),
                _decimal(tax, "baseImponible"),
                _decimal(tax, "valorImpuesto"),
                _decimal(tax, "tarifa"),
            )
            for tax in support.iterfind("impuestosDocSustento/impuestoDocSustento")
        ),
        retentions=tuple(_retention(held) for held in support.iterfind("retenciones/retencion")),
        payments=_payments(support),
        registered_on=_date(registered) if registered else None,
        abroad=_abroad(support),
    )


def _withholding(root: etree._Element, info: etree._Element, email: str | None) -> dict:
    subject_type = _text(info, "tipoSujetoRetenido")
    return {
        "subject": _party(info, PARTIES[VoucherKind.WITHHOLDING], email),
        "fiscal_period": datetime.strptime(
            _text(info, "periodoFiscal"), FISCAL_PERIOD_FORMAT
        ).date(),
        "documents": tuple(_support(held) for held in root.iterfind("docsSustento/docSustento"))
        or _legacy_documents(root),
        "related_party": _text(info, "parteRel") == YES,
        "subject_type": _listed(SubjectType, subject_type) if subject_type else None,
    }


def _voucher(
    root: etree._Element,
    kind: VoucherKind,
    tributaria: etree._Element,
    info: etree._Element,
    extra: dict[str, str],
) -> AnyVoucher:
    numbered = _numbered(kind, tributaria, info)
    email = extra.get(EMAIL_FIELD)
    rest = tuple(
        (name, value)
        for name, value in extra.items()
        if name not in (EMAIL_FIELD, SYSTEM_PROVIDER_FIELD)
    )
    if kind == VoucherKind.REMISSION_GUIDE:
        return RemissionGuide(**numbered, **_guide(root, info, email), extra=rest)
    if kind == VoucherKind.WITHHOLDING:
        return Withholding(**numbered, **_withholding(root, info, email), extra=rest)
    return Voucher(**numbered, **_priced(root, kind, info, email), extra=rest)


def read_voucher(document: bytes) -> AnyVoucher:
    """The voucher a signed or unsigned document carries."""
    root = etree.fromstring(document, PARSER)
    return _voucher(root, *_parts(root))


def _total(voucher: AnyVoucher, info: etree._Element) -> Decimal:
    if isinstance(voucher, Withholding):
        return voucher.withheld
    tag = TOTAL_TAGS.get(voucher.kind)
    return _decimal(info, tag) if tag else ZERO


def read(record: str) -> Issued:
    held = etree.fromstring(record.encode(), PARSER)
    authorized_at = _text(held, "fechaAutorizacion")
    if not authorized_at:
        raise VoucherInvalid("The record holds no authorized voucher")
    root = etree.fromstring(_text(held, "comprobante").encode(), PARSER)
    kind, tributaria, info, extra = _parts(root)
    voucher = _voucher(root, kind, tributaria, info, extra)
    legend = _text(tributaria, RIMPE_TYPE)
    issuer = Issuer(
        legal_name=_text(tributaria, "razonSocial"),
        ruc=_text(tributaria, "ruc"),
        head_office_address=_text(tributaria, "dirMatriz"),
        keeps_accounts=_text(info, "obligadoContabilidad") == YES,
        trade_name=_text(tributaria, "nombreComercial") or None,
        special_taxpayer=_text(info, "contribuyenteEspecial") or None,
        withholding_agent=_text(tributaria, "agenteRetencion") or None,
        rimpe=bool(legend),
        system_provider_ruc=extra.get(SYSTEM_PROVIDER_FIELD),
        popular_business=legend == POPULAR_BUSINESS_LEGEND,
    )
    return Issued(
        voucher=voucher,
        issuer=issuer,
        authorized_at=datetime.fromisoformat(authorized_at),
        authorization_number=_text(held, "numeroAutorizacion"),
        total=_total(voucher, info),
        ice=_tax(info, kind, ICE_TAX),
        irbpnr=_tax(info, kind, IRBPNR_TAX),
        tip=_decimal(info, "propina") if kind == VoucherKind.INVOICE else None,
    )
