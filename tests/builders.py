from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from eqinvoice import (
    Buyer,
    BuyerKind,
    Environment,
    Establishment,
    Issued,
    Issuer,
    Line,
    Modified,
    Payment,
    PaymentForm,
    SimulatedSri,
    Taxpayer,
    Voucher,
    VoucherKind,
    access_key,
    read,
    sign_voucher,
    simulated_credential,
)
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


def a_voucher(kind: VoucherKind = VoucherKind.INVOICE, **overrides: Any) -> Voucher:
    fields: dict[str, Any] = {
        "kind": kind,
        "environment": Environment.TEST,
        "access_key": access_key(ISSUED_ON, kind, RUC, Environment.TEST, "001", "001", 7),
        "establishment": "001",
        "point": "001",
        "sequential": 7,
        "establishment_address": "AV. UNIVERSITARIA 10-20, LOJA",
        "issued_on": ISSUED_ON,
        "buyer": Buyer(BuyerKind.CEDULA, "1104680135", "Ana Perez & Hijos", "ana@example.com"),
        "lines": (a_line(), UNTAXED),
        "payments": (Payment(CASH, Decimal("10.00")), Payment(TRANSFER, Decimal("3.30"))),
    }
    if kind == VoucherKind.CREDIT_NOTE:
        fields["modified"] = Modified("001-001-000000003", date(2026, 10, 20), "Devolucion")
        fields["payments"] = ()
    return Voucher(**{**fields, **overrides})


def signed(voucher: Voucher, issuer: Issuer | None = None) -> bytes:
    return sign_voucher(voucher, issuer or an_issuer(), simulated_credential(), NOW)


def authorized(voucher: Voucher, issuer: Issuer | None = None) -> str:
    sri = SimulatedSri()
    sri.send(signed(voucher, issuer))
    answer = sri.authorize(voucher.access_key)
    assert answer is not None
    return answer.record


def issued(kind: VoucherKind = VoucherKind.INVOICE, **overrides: Any) -> Issued:
    return read(authorized(a_voucher(kind, **overrides)))
