from __future__ import annotations

from decimal import Decimal

import pytest

from eqinvoice import Payment, VoucherInvalid, VoucherKind, read, read_voucher
from tests.builders import RUC, a_voucher, authorized, signed


def test_the_record_reads_back_as_the_voucher_it_holds():
    voucher = a_voucher()
    issued = read(authorized(voucher))
    assert issued.voucher.number == "001-001-000000007"
    assert issued.voucher.buyer == voucher.buyer
    assert issued.voucher.lines == voucher.lines
    assert issued.voucher.payments == (
        Payment("01", Decimal("10.00")),
        Payment("20", Decimal("3.30")),
    )
    assert issued.issuer.ruc == RUC and issued.issuer.system_provider_ruc == RUC
    assert issued.authorization_number == voucher.access_key
    assert issued.total == Decimal("13.30")


def test_an_invoice_reads_its_tip_and_other_taxes_a_credit_note_no_tip():
    for kind, tip in ((VoucherKind.INVOICE, Decimal("0")), (VoucherKind.CREDIT_NOTE, None)):
        issued = read(authorized(a_voucher(kind)))
        assert (issued.ice, issued.irbpnr, issued.tip) == (Decimal("0"), Decimal("0"), tip)


def test_a_credit_note_reads_the_invoice_it_modifies():
    voucher = a_voucher(VoucherKind.CREDIT_NOTE)
    assert read(authorized(voucher)).voucher.modified == voucher.modified


def test_a_signed_document_reads_back_as_the_voucher_it_carries():
    voucher = a_voucher(VoucherKind.CREDIT_NOTE)
    assert read_voucher(signed(voucher)) == voucher


def test_a_record_with_no_voucher_inside_is_refused():
    with pytest.raises(VoucherInvalid):
        read("<autorizacion><comprobante><![CDATA[<retencion/>]]></comprobante></autorizacion>")
