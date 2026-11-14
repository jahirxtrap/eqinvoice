from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from eqinvoice import VoucherInvalid, VoucherKind, period_sales, price_line
from tests.builders import a_voucher

SERVICE = price_line("1", "Desarrollo", Decimal("1"), Decimal("500"), "4")
EXEMPT = price_line("2", "Capacitacion", Decimal("1"), Decimal("80"), "7")
REFUND = price_line("1", "Desarrollo", Decimal("1"), Decimal("100"), "4")
NOVEMBER = (date(2026, 11, 1), date(2026, 11, 30))


def test_a_month_adds_up_its_vouchers_per_rate_and_credit_notes_offset_them():
    vouchers = (
        a_voucher(issued_on=date(2026, 11, 20), lines=(SERVICE, EXEMPT)),
        a_voucher(issued_on=date(2026, 11, 25), lines=(SERVICE,)),
        a_voucher(VoucherKind.CREDIT_NOTE, issued_on=date(2026, 11, 28), lines=(REFUND,)),
        a_voucher(issued_on=date(2026, 12, 1), lines=(SERVICE,)),
    )
    sales = period_sales(vouchers, *NOVEMBER)
    assert (sales.invoices, sales.credit_notes) == (2, 1)
    taxed, exempt = sales.rates
    assert (taxed.code, taxed.rate) == ("4", Decimal("15"))
    assert (taxed.gross, taxed.net, taxed.vat) == (Decimal("1000"), Decimal("900"), Decimal("135"))
    assert (exempt.code, exempt.gross, exempt.net, exempt.vat) == (
        "7",
        Decimal("80"),
        Decimal("80"),
        Decimal("0"),
    )
    assert sales.vat == Decimal("135")


def test_a_period_without_vouchers_adds_up_to_nothing():
    sales = period_sales((), *NOVEMBER)
    assert (sales.invoices, sales.credit_notes, sales.rates, sales.vat) == (0, 0, (), Decimal("0"))


def test_a_period_that_ends_before_it_starts_is_refused():
    with pytest.raises(VoucherInvalid):
        period_sales((), date(2026, 11, 30), date(2026, 11, 1))
