from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from lxml import etree

from eqinvoice import (
    Payment,
    Reference,
    RetentionTax,
    Voucher,
    VoucherInvalid,
    VoucherKind,
    Withholding,
    read,
    read_voucher,
)
from tests.builders import (
    CASH,
    FIXTURES,
    RUC,
    TRANSFER,
    a_document,
    a_voucher,
    an_issuer,
    authorized,
    signed,
)

ELSEWHERE = FIXTURES / "bryancalisto"


def test_a_payment_form_the_sri_does_not_list_is_refused():
    document = signed(a_voucher()).replace(
        b"<formaPago>01</formaPago>", b"<formaPago>05</formaPago>"
    )
    with pytest.raises(VoucherInvalid):
        read_voucher(document)


def test_the_record_reads_back_whether_its_issuer_is_a_popular_business():
    popular = read(authorized(a_voucher(), an_issuer(rimpe=True, popular_business=True))).issuer
    assert popular.rimpe and popular.popular_business
    entrepreneur = read(authorized(a_voucher(), an_issuer(rimpe=True))).issuer
    assert entrepreneur.rimpe and not entrepreneur.popular_business


@pytest.mark.parametrize("kind", list(VoucherKind))
def test_every_kind_reads_back_as_the_voucher_it_was_built_from(kind):
    voucher = a_document(kind)
    assert read_voucher(signed(voucher)) == voucher
    assert read(authorized(voucher)).voucher == voucher


def test_the_record_reads_back_as_the_voucher_it_holds():
    voucher = a_voucher()
    issued = read(authorized(voucher))
    assert isinstance(issued.voucher, Voucher)
    assert issued.voucher.number == "001-001-000000007"
    assert issued.voucher.payments == (
        Payment(CASH, Decimal("10.00")),
        Payment(TRANSFER, Decimal("3.30")),
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
    held = read(authorized(voucher)).voucher
    assert isinstance(held, Voucher) and held.modified == voucher.modified


def test_a_signed_document_reads_back_as_the_voucher_it_carries():
    voucher = a_voucher(VoucherKind.CREDIT_NOTE)
    assert read_voucher(signed(voucher)) == voucher


def test_a_record_with_no_voucher_inside_is_refused():
    with pytest.raises(VoucherInvalid):
        read("<autorizacion><comprobante><![CDATA[<retencion/>]]></comprobante></autorizacion>")


@pytest.mark.parametrize(
    ("name", "kind", "total"),
    (
        ("invoice", VoucherKind.INVOICE, Decimal("44.56")),
        ("credit-note", VoucherKind.CREDIT_NOTE, Decimal("115.00")),
        ("debit-note", VoucherKind.DEBIT_NOTE, Decimal("18.66")),
    ),
)
def test_a_voucher_another_signer_wrote_reads_to_its_total(name, kind, total):
    voucher = read_voucher((ELSEWHERE / f"{name}.xml").read_bytes())
    assert isinstance(voucher, Voucher) and (voucher.kind, voucher.total) == (kind, total)


def test_a_debit_note_reason_that_no_taxed_base_takes_reads_untaxed():
    voucher = read_voucher((ELSEWHERE / "debit-note.xml").read_bytes())
    assert isinstance(voucher, Voucher)
    assert [(line.description, line.rate_code, line.vat) for line in voucher.lines] == [
        ("INTERESES", "0", Decimal("0")),
        ("GASTO DE COBRANZA", "4", Decimal("2.35")),
    ]


def test_a_debit_note_whose_reasons_cannot_fill_its_taxed_bases_is_refused():
    document = signed(a_voucher(VoucherKind.DEBIT_NOTE)).replace(
        b"<baseImponible>10.00</baseImponible>", b"<baseImponible>12.00</baseImponible>"
    )
    with pytest.raises(VoucherInvalid, match="taxed bases"):
        read_voucher(document)


def test_an_old_withholding_gathers_its_retentions_by_the_document_each_names():
    root = etree.fromstring((ELSEWHERE / "withholding-1.0.0.xml").read_bytes())
    taxes = root.find("impuestos")
    first = taxes.find("impuesto")
    other = deepcopy(first)
    other.find("numDocSustento").text = "001001000000002"
    taxes.extend((deepcopy(first), other))
    withholding = read_voucher(etree.tostring(root))
    assert isinstance(withholding, Withholding)
    sold, later = withholding.documents
    assert sold.document == Reference("01", "001-001-000000001", date(2023, 7, 21))
    assert later.document.number == "001-001-000000002"
    assert [(held.tax, held.code, held.withheld) for held in sold.retentions] == [
        (RetentionTax.INCOME, "1", Decimal("10.00")),
    ] * 2
    assert withholding.withheld == Decimal("30.00")
