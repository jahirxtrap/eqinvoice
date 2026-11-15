from __future__ import annotations

from decimal import Decimal

import pytest

from eqinvoice import RetentionTax, VoucherInvalid, price_line, tax_on, withhold
from tests.builders import a_voucher, signed


def test_the_subtotal_and_the_iva_round_to_the_cent_with_halves_up():
    line = price_line("1", "Desarrollo", Decimal("3"), Decimal("0.335"), "4")
    assert (line.subtotal, line.vat) == (Decimal("1.01"), Decimal("0.15"))


def test_the_discount_comes_off_before_the_iva():
    line = price_line("1", "Desarrollo", Decimal("1"), Decimal("500"), "4", Decimal("100"))
    assert (line.subtotal, line.vat, line.rate_percentage) == (
        Decimal("400.00"),
        Decimal("60.00"),
        Decimal("15"),
    )


def test_an_exempt_line_carries_no_iva():
    line = price_line("1", "Consulta", Decimal("2"), Decimal("12.50"), "7")
    assert (line.subtotal, line.vat) == (Decimal("25.00"), Decimal("0.00"))


def test_a_rate_the_sri_does_not_have_is_refused():
    with pytest.raises(VoucherInvalid):
        price_line("1", "Desarrollo", Decimal("1"), Decimal("1"), "99")


def test_a_priced_line_satisfies_the_sris_schema():
    signed(a_voucher(lines=(price_line("1", "Desarrollo", Decimal("1"), Decimal("500"), "4"),)))


def test_a_base_takes_the_iva_of_its_rate_to_the_cent():
    taxed = tax_on(Decimal("33.335"), "4")
    assert (taxed.base, taxed.vat, taxed.rate) == (Decimal("33.34"), Decimal("5.00"), Decimal("15"))


def test_an_iva_retention_takes_its_percentage_from_the_sris_code():
    retention = withhold(RetentionTax.VAT, "2", Decimal("15.00"))
    assert (retention.percentage, retention.withheld) == (Decimal("70"), Decimal("10.50"))
    with pytest.raises(VoucherInvalid):
        withhold(RetentionTax.VAT, "2", Decimal("15.00"), Decimal("30"))
    with pytest.raises(VoucherInvalid):
        withhold(RetentionTax.VAT, "99", Decimal("15.00"))


def test_an_income_retention_brings_its_percentage():
    retention = withhold(RetentionTax.INCOME, "312", Decimal("200"), Decimal("1.75"))
    assert retention.withheld == Decimal("3.50")
    with pytest.raises(VoucherInvalid):
        withhold(RetentionTax.INCOME, "312", Decimal("200"))
