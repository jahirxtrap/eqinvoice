from __future__ import annotations

from decimal import Decimal

import pytest
from lxml import etree

from eqinvoice import (
    Buyer,
    BuyerKind,
    Payment,
    VoucherInvalid,
    VoucherKind,
    build,
    issuer_legend,
    rimpe_legend,
)
from tests.builders import CASH, RUC, TRANSFER, a_line, a_voucher, an_issuer, signed


def tree_of(**overrides) -> etree._Element:
    return etree.fromstring(signed(a_voucher(**overrides)))


def test_an_invoice_and_a_credit_note_satisfy_the_sris_schema():
    for kind in VoucherKind:
        signed(a_voucher(kind))


def test_one_the_schema_would_refuse_never_leaves():
    with pytest.raises(VoucherInvalid):
        signed(a_voucher(lines=(a_line(description="x" * 301),)))


def test_a_credit_note_names_the_invoice_it_modifies():
    with pytest.raises(VoucherInvalid):
        build(a_voucher(VoucherKind.CREDIT_NOTE, modified=None), an_issuer())


def test_the_final_consumer_is_named_as_the_sri_names_it():
    tree = tree_of(buyer=Buyer(BuyerKind.FINAL_CONSUMER))
    assert tree.findtext("infoFactura/tipoIdentificacionComprador") == "07"
    assert tree.findtext("infoFactura/identificacionComprador") == "9999999999999"
    assert tree.findtext("infoFactura/razonSocialComprador") == "CONSUMIDOR FINAL"


def test_the_payments_add_up_per_form():
    payments = (
        Payment(TRANSFER, Decimal("5.00")),
        Payment(CASH, Decimal("5.00")),
        Payment(TRANSFER, Decimal("3.30")),
    )
    tree = tree_of(payments=payments)
    paid = {p.findtext("formaPago"): p.findtext("total") for p in tree.iterfind(".//pago")}
    assert paid == {"20": "8.30", "01": "5.00"}


def test_each_rate_adds_up_its_own_base_and_tax():
    tree = tree_of()
    groups = {
        t.findtext("codigoPorcentaje"): (t.findtext("baseImponible"), t.findtext("valor"))
        for t in tree.iterfind("infoFactura/totalConImpuestos/totalImpuesto")
    }
    assert groups == {"4": ("8.70", "1.30"), "0": ("3.30", "0.00")}
    assert tree.findtext("infoFactura/importeTotal") == "13.30"


def test_the_email_and_the_system_provider_ride_in_the_extra_fields():
    extra = {f.get("nombre"): f.text for f in tree_of().iterfind("infoAdicional/campoAdicional")}
    assert extra == {"Email": "ana@example.com", "RUC Proveedor": RUC}


def test_no_more_extra_fields_than_the_schema_takes():
    fields = tuple((f"Campo {index}", "valor") for index in range(20))
    assert len(tree_of(extra=fields).findall("infoAdicional/campoAdicional")) == 15


def test_a_rimpe_taxpayer_carries_the_legend_its_schema_fixes():
    tree = etree.fromstring(signed(a_voucher(), an_issuer(rimpe=True)))
    assert tree.findtext("infoTributaria/contribuyenteRimpe") == rimpe_legend()
    assert rimpe_legend().startswith("CONTRIBUYENTE")


@pytest.mark.parametrize("kind", list(VoucherKind))
def test_a_popular_business_carries_the_legend_the_ficha_tecnica_requires(kind):
    issuer = an_issuer(rimpe=True, popular_business=True)
    tree = etree.fromstring(signed(a_voucher(kind), issuer))
    assert tree.findtext("infoTributaria/contribuyenteRimpe") == issuer_legend(issuer)
    assert issuer_legend(issuer) == "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"
    assert issuer_legend(an_issuer()) is None
