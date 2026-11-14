from __future__ import annotations

from decimal import Decimal

import pytest

from eqinvoice import VoucherInvalid, price_line
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
