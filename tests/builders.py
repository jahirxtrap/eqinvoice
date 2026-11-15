from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from eqinvoice import (
    AnyVoucher,
    Buyer,
    BuyerKind,
    Environment,
    Establishment,
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
    RetentionTax,
    SimulatedSri,
    SupportDocument,
    Taxpayer,
    Voucher,
    VoucherKind,
    Withholding,
    access_key,
    price_line,
    read,
    sign_voucher,
    simulated_credential,
    tax_on,
    withhold,
)
from eqinvoice.protocol import NOTES
from eqinvoice.ride import MarkLogo

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime.now(UTC)
RUC = "1790011223001"
ISSUED_ON = date(2026, 10, 27)
CASH = PaymentForm.WITHOUT_FINANCIAL_SYSTEM
TRANSFER = PaymentForm.WITH_FINANCIAL_SYSTEM
LOGO = MarkLogo(
    mark=FIXTURES / "mark.svg",
    name=FIXTURES / "name.svg",
    font=FIXTURES / "PlusJakartaSans-ExtraBold.ttf",
)


def an_issuer(**overrides: Any) -> Issuer:
    fields: dict[str, Any] = {
        "legal_name": "ACME COMERCIAL S.A.S.",
        "ruc": RUC,
        "head_office_address": "AV. UNIVERSITARIA 10-20, LOJA",
        "keeps_accounts": True,
        "trade_name": "Acme Shop",
        "system_provider_ruc": RUC,
    }
    return Issuer(**{**fields, **overrides})


def a_taxpayer(**overrides: Any) -> Taxpayer:
    fields: dict[str, Any] = {
        "ruc": RUC,
        "legal_name": "ACME COMERCIAL S.A.S.",
        "active": True,
        "regime": "GENERAL",
        "keeps_accounts": True,
        "withholding_agent": False,
        "special_taxpayer": False,
        "activity": "VENTA AL POR MAYOR DE COMPUTADORAS",
        "establishments": (
            Establishment("001", "LOJA / LOJA / SUCRE / AV. UNIVERSITARIA 10-20", None, True, True),
            Establishment(
                "002", "LOJA / LOJA / EL VALLE / AV. SALVADOR BUSTAMANTE", None, False, False
            ),
        ),
    }
    return Taxpayer(**{**fields, **overrides})


def a_line(**overrides: Any) -> Line:
    fields: dict[str, Any] = {
        "code": "SKU-1",
        "description": "Cafe molido (500 g)",
        "quantity": Decimal("2"),
        "unit_price": Decimal("4.347826"),
        "discount": Decimal("0"),
        "subtotal": Decimal("8.70"),
        "vat": Decimal("1.30"),
        "rate_code": "4",
        "rate_percentage": Decimal("15.00"),
    }
    return Line(**{**fields, **overrides})


UNTAXED = a_line(
    code="SKU-2",
    description="Arroz (1 kg)",
    quantity=Decimal("3"),
    unit_price=Decimal("1.100000"),
    subtotal=Decimal("3.30"),
    vat=Decimal("0"),
    rate_code="0",
    rate_percentage=Decimal("0.00"),
)


CHARGES = (
    price_line("", "Intereses por mora", Decimal("1"), Decimal("10.00"), "4"),
    price_line("", "Gastos de cobranza", Decimal("1"), Decimal("3.30"), "0"),
)
SUPPLIER = Buyer(
    BuyerKind.CEDULA, "1104680135", "Ana Perez", "ana@example.com", "BARRIO SAN ISIDRO, LOJA"
)
SOLD = Reference(
    "01",
    "001-001-000000007",
    ISSUED_ON,
    access_key(ISSUED_ON, VoucherKind.INVOICE, RUC, Environment.TEST, "001", "001", 7),
)


def _numbered(kind: VoucherKind) -> dict[str, Any]:
    return {
        "kind": kind,
        "environment": Environment.TEST,
        "access_key": access_key(ISSUED_ON, kind, RUC, Environment.TEST, "001", "001", 7),
        "establishment": "001",
        "point": "001",
        "sequential": 7,
        "establishment_address": "AV. UNIVERSITARIA 10-20, LOJA",
        "issued_on": ISSUED_ON,
    }


def a_voucher(kind: VoucherKind = VoucherKind.INVOICE, **overrides: Any) -> Voucher:
    fields: dict[str, Any] = {
        **_numbered(kind),
        "buyer": Buyer(BuyerKind.CEDULA, "1104680135", "Ana Perez & Hijos", "ana@example.com"),
        "lines": (a_line(), UNTAXED),
        "payments": (Payment(CASH, Decimal("10.00")), Payment(TRANSFER, Decimal("3.30"))),
    }
    if kind in NOTES:
        fields["modified"] = Modified("001-001-000000003", date(2026, 10, 20), "Devolucion")
        fields["payments"] = ()
    if kind == VoucherKind.DEBIT_NOTE:
        fields["lines"] = CHARGES
        fields["payments"] = (Payment(TRANSFER, Decimal("14.80")),)
        fields["modified"] = Modified("001-001-000000003", date(2026, 10, 20), "")
    if kind == VoucherKind.PURCHASE_SETTLEMENT:
        fields["buyer"] = SUPPLIER
    return Voucher(**{**fields, **overrides})


def a_guide(**overrides: Any) -> RemissionGuide:
    fields: dict[str, Any] = {
        **_numbered(VoucherKind.REMISSION_GUIDE),
        "carrier": Buyer(
            BuyerKind.RUC, "1790011224001", "TRANSPORTES DEL SUR S.A.", "flota@example.com"
        ),
        "plate": "LBA-1234",
        "departure": "AV. UNIVERSITARIA 10-20, LOJA",
        "starts_on": ISSUED_ON,
        "ends_on": date(2026, 10, 28),
        "recipients": (
            Recipient(
                identification="1104680135",
                name="Ana Perez & Hijos",
                address="CALLE SUCRE 5-12, ZAMORA",
                reason="Venta",
                items=(
                    GuideItem(
                        "Cafe molido (500 g)", Decimal("2"), "SKU-1", details=(("Lote", "A-12"),)
                    ),
                    GuideItem("Arroz (1 kg)", Decimal("3"), "SKU-2"),
                ),
                route="Loja - Zamora",
                supporting=SOLD,
            ),
        ),
    }
    return RemissionGuide(**{**fields, **overrides})


def a_withholding(**overrides: Any) -> Withholding:
    purchase = SupportDocument(
        support_code="01",
        document=Reference("01", "002-001-000000045", date(2026, 10, 25), SOLD.authorization),
        taxes=(tax_on(Decimal("100"), "4"),),
        retentions=(
            withhold(RetentionTax.INCOME, "303", Decimal("100"), Decimal("10")),
            withhold(RetentionTax.VAT, "1", Decimal("15")),
        ),
        payments=(Payment(TRANSFER, Decimal("115.00")),),
        registered_on=ISSUED_ON,
    )
    fields: dict[str, Any] = {
        **_numbered(VoucherKind.WITHHOLDING),
        "subject": Buyer(
            BuyerKind.RUC, "1790011224001", "PROVEEDORA DEL SUR S.A.", "pagos@example.com"
        ),
        "fiscal_period": ISSUED_ON.replace(day=1),
        "documents": (purchase,),
    }
    return Withholding(**{**fields, **overrides})


def a_document(kind: VoucherKind = VoucherKind.INVOICE, **overrides: Any) -> AnyVoucher:
    if kind == VoucherKind.REMISSION_GUIDE:
        return a_guide(**overrides)
    if kind == VoucherKind.WITHHOLDING:
        return a_withholding(**overrides)
    return a_voucher(kind, **overrides)


def signed(voucher: AnyVoucher, issuer: Issuer | None = None) -> bytes:
    return sign_voucher(voucher, issuer or an_issuer(), simulated_credential(), NOW)


def authorized(voucher: AnyVoucher, issuer: Issuer | None = None) -> str:
    sri = SimulatedSri()
    sri.send(signed(voucher, issuer))
    answer = sri.authorize(voucher.access_key)
    assert answer is not None
    return answer.record


def issued(kind: VoucherKind = VoucherKind.INVOICE, **overrides: Any) -> Issued:
    return read(authorized(a_document(kind, **overrides)))
