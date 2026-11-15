from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from eqinvoice import (
    Authorization,
    BuyerKind,
    CredentialRefused,
    Environment,
    Establishment,
    GuideItem,
    Message,
    Payment,
    Reception,
    Recipient,
    Reference,
    RemissionGuide,
    RetentionTax,
    SimulatedRegistry,
    SimulatedSri,
    Sri,
    SriUnavailable,
    SupportDocument,
    Taxpayer,
    Voucher,
    VoucherInvalid,
    VoucherKind,
    Withholding,
    price_line,
    simulated_credential,
    tax_on,
    withhold,
)
from eqinvoice_mcp.book import Book, NotInBook
from eqinvoice_mcp.desk import Desk, Refused
from eqinvoice_mcp.model import Standing
from tests.builders import NOW, RUC, TRANSFER, a_taxpayer

CLIENT_RUC = "1790011224001"
SERVICE = price_line("DEV", "Desarrollo de software", Decimal("1"), Decimal("500"), "4")
RETURNED = Message("35", "ARCHIVO NO CUMPLE ESTRUCTURA XML", "", "ERROR")


class Unreachable:
    def send(self, document: bytes) -> Reception:
        raise SriUnavailable("no route")

    def authorize(self, access_key: str) -> Authorization | None:
        raise SriUnavailable("no route")


class Returning:
    def send(self, document: bytes) -> Reception:
        return Reception(received=False, messages=(RETURNED,))

    def authorize(self, access_key: str) -> Authorization | None:
        return None


def a_desk(
    root: Path, sri: Sri | None = None, clock=lambda: NOW, issuer: Taxpayer | None = None
) -> Desk:
    taxpayers = (issuer or a_taxpayer(), a_taxpayer(ruc=CLIENT_RUC, legal_name="CLIENTE S.A.S."))
    held = sri or SimulatedSri()
    return Desk(
        Book(root),
        SimulatedRegistry(*taxpayers),
        lambda environment: held,
        simulated_credential,
        clock=clock,
        wait=lambda seconds: None,
    )


def a_configured_desk(root: Path, sri: Sri | None = None) -> Desk:
    desk = a_desk(root, sri)
    desk.configure(RUC, Environment.TEST, "001", "100")
    desk.register_client(CLIENT_RUC, "pagos@cliente.ec")
    return desk


def test_the_issuer_is_configured_as_the_registry_holds_it(tmp_path):
    profile = a_desk(tmp_path).configure(RUC, Environment.TEST, "001", "100")
    assert profile.issuer.legal_name == "ACME COMERCIAL S.A.S." and profile.issuer.keeps_accounts
    assert profile.establishment_address == "LOJA / LOJA / SUCRE / AV. UNIVERSITARIA 10-20"
    assert Book(tmp_path).profile() == profile


def test_the_issuer_takes_the_trade_name_of_its_establishment(tmp_path):
    offices = (
        Establishment(
            "001", "LOJA / LOJA / SUCRE / AV. UNIVERSITARIA", "ACME COMERCIAL S.A.S.", True, True
        ),
        Establishment(
            "002", "LOJA / LOJA / EL VALLE / AV. SALVADOR BUSTAMANTE", "ACME SHOP", False, True
        ),
    )
    desk = a_desk(tmp_path, issuer=a_taxpayer(establishments=offices))
    assert desk.configure(RUC, Environment.TEST, "002", "100").issuer.trade_name == "ACME SHOP"
    assert desk.configure(RUC, Environment.TEST, "001", "100").issuer.trade_name is None


def test_an_agent_or_special_taxpayer_issues_with_its_numbers(tmp_path):
    agent = a_taxpayer(withholding_agent=True, special_taxpayer=True)
    desk = a_desk(tmp_path, issuer=agent)
    with pytest.raises(VoucherInvalid):
        desk.configure(RUC, Environment.TEST, "001", "100")
    issuer = desk.configure(RUC, Environment.TEST, "001", "100", "1", "5368", RUC).issuer
    assert (issuer.withholding_agent, issuer.special_taxpayer) == ("1", "5368")
    assert issuer.system_provider_ruc == RUC


def test_an_unknown_ruc_or_a_closed_establishment_cannot_issue(tmp_path):
    desk = a_desk(tmp_path)
    with pytest.raises(Refused):
        desk.configure("1100000000001", Environment.TEST, "001", "100")
    with pytest.raises(Refused):
        desk.configure(RUC, Environment.TEST, "002", "100")
    with pytest.raises(NotInBook):
        desk.profile()


def test_a_ruc_client_is_named_by_the_registry_and_any_other_by_hand(tmp_path):
    desk = a_desk(tmp_path)
    assert desk.register_client(CLIENT_RUC, name="Otro").name == "CLIENTE S.A.S."
    with pytest.raises(Refused):
        desk.register_client("1104680135")
    person = desk.register_client("1104680135", "ana@example.com", "Ana Perez")
    assert person.kind == BuyerKind.CEDULA
    assert {buyer.identification for buyer in desk.clients()} == {CLIENT_RUC, "1104680135"}
    assert desk.forget_client("1104680135") == (desk.clients()[0],)


def test_a_draft_comes_with_the_voucher_it_would_be_and_a_printed_preview(tmp_path):
    previewed = a_configured_desk(tmp_path).draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER)
    voucher = previewed.voucher
    assert isinstance(voucher, Voucher)
    assert voucher.number == "001-100-000000001" and voucher.total == Decimal("575.00")
    assert voucher.payments == (Payment(TRANSFER, Decimal("575.00")),)
    assert voucher.buyer.email == "pagos@cliente.ec"
    assert previewed.preview.read_bytes().startswith(b"%PDF")


def test_a_draft_needs_a_line_and_a_client_in_the_book(tmp_path):
    desk = a_configured_desk(tmp_path)
    with pytest.raises(Refused):
        desk.draft_invoice(CLIENT_RUC, (), TRANSFER)
    with pytest.raises(NotInBook):
        desk.draft_invoice("0999999999", (SERVICE,), TRANSFER)


def test_issuing_numbers_signs_and_files_each_voucher_once_authorized(tmp_path):
    desk = a_configured_desk(tmp_path)
    first = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    second = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    assert first.standing == second.standing == Standing.AUTHORIZED
    assert (first.voucher.number, second.voucher.number) == (
        "001-100-000000001",
        "001-100-000000002",
    )
    assert first.printed is not None and first.printed.read_bytes().startswith(b"%PDF")
    assert desk.drafts() == ()
    assert [filed.voucher.number for filed in desk.vouchers()] == [
        "001-100-000000001",
        "001-100-000000002",
    ]


def test_a_voucher_the_sri_returns_is_filed_as_refused_with_its_reasons(tmp_path):
    desk = a_configured_desk(tmp_path, Returning())
    filed = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    assert filed.standing == Standing.REFUSED and filed.messages == (RETURNED,)
    assert desk.check(filed.voucher.number) == filed


def test_an_unreachable_sri_leaves_the_voucher_pending_until_it_is_checked(tmp_path):
    desk = a_configured_desk(tmp_path, Unreachable())
    draft = desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft
    with pytest.raises(SriUnavailable, match="001-100-000000001 is filed as pending"):
        desk.issue(draft.identifier)
    assert desk.vouchers()[0].standing == Standing.PENDING
    checked = a_desk(tmp_path).check("001-100-000000001")
    assert checked.standing == Standing.AUTHORIZED and checked.printed is not None


def test_a_signature_that_cannot_sign_gives_its_number_back(tmp_path):
    a_configured_desk(tmp_path)
    draft = a_desk(tmp_path).draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft
    late = a_desk(tmp_path, clock=lambda: NOW + timedelta(days=365 * 20))
    with pytest.raises(CredentialRefused):
        late.issue(draft.identifier)
    assert a_desk(tmp_path).issue(draft.identifier).voucher.number == "001-100-000000001"


def test_a_credit_note_offsets_an_authorized_invoice_and_never_exceeds_it(tmp_path):
    desk = a_configured_desk(tmp_path)
    invoice = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    number = invoice.voucher.number
    whole = desk.draft_credit_note(number, "Devolucion")
    note, sold = whole.voucher, invoice.voucher
    assert isinstance(note, Voucher) and isinstance(sold, Voucher)
    assert note.kind == VoucherKind.CREDIT_NOTE and note.payments == ()
    assert note.modified is not None and note.modified.number == number
    assert note.total == sold.total
    with pytest.raises(Refused):
        desk.draft_credit_note(number, "Devolucion", (replace(SERVICE, subtotal=Decimal("600")),))
    issued = desk.issue(whole.draft.identifier)
    assert issued.standing == Standing.AUTHORIZED and issued.voucher.number == "001-100-000000001"
    assert desk.check(issued.voucher.number, VoucherKind.CREDIT_NOTE) == issued
    with pytest.raises(NotInBook):
        desk.draft_credit_note("001-100-000000009", "Devolucion")


def test_the_sales_add_up_what_the_sri_authorized_and_list_what_awaits_it(tmp_path):
    desk = a_configured_desk(tmp_path)
    invoice = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    desk.issue(desk.draft_credit_note(invoice.voucher.number, "Devolucion").draft.identifier)
    desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    late = a_desk(tmp_path, Unreachable())
    with pytest.raises(SriUnavailable):
        late.issue(late.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    day = invoice.voucher.issued_on
    report = desk.sales(day, day)
    assert (report.sales.invoices, report.sales.credit_notes) == (2, 1)
    (taxed,) = report.sales.rates
    assert (taxed.gross, taxed.net, taxed.vat) == (Decimal("1000"), Decimal("500"), Decimal("75"))
    assert [(held.kind, held.number) for held in report.pending] == [
        (VoucherKind.INVOICE, "001-100-000000003")
    ]


def test_a_credit_note_needs_an_invoice_the_sri_authorized(tmp_path):
    desk = a_configured_desk(tmp_path, Returning())
    refused = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    with pytest.raises(Refused):
        desk.draft_credit_note(refused.voucher.number, "Devolucion")


def test_a_debit_note_charges_an_authorized_invoice_and_adds_to_its_sales(tmp_path):
    desk = a_configured_desk(tmp_path)
    invoice = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    late = price_line("", "Intereses por mora", Decimal("1"), Decimal("20"), "4")
    note = desk.issue(
        desk.draft_debit_note(invoice.voucher.number, (late,), TRANSFER).draft.identifier
    )
    assert note.standing == Standing.AUTHORIZED and note.voucher.number == "001-100-000000001"
    assert isinstance(note.voucher, Voucher) and note.voucher.total == Decimal("23.00")
    day = invoice.voucher.issued_on
    (taxed,) = desk.sales(day, day).sales.rates
    assert (taxed.gross, taxed.vat) == (Decimal("520"), Decimal("78"))


def test_a_purchase_settlement_pays_a_supplier_without_ruc(tmp_path):
    desk = a_configured_desk(tmp_path)
    desk.register_client("1104680135", name="Ana Perez", address="BARRIO SAN ISIDRO, LOJA")
    filed = desk.issue(
        desk.draft_purchase_settlement("1104680135", (SERVICE,), TRANSFER).draft.identifier
    )
    assert filed.standing == Standing.AUTHORIZED
    assert isinstance(filed.voucher, Voucher)
    assert filed.voucher.buyer.address == "BARRIO SAN ISIDRO, LOJA"
    assert desk.sales(filed.voucher.issued_on, filed.voucher.issued_on).sales.rates == ()


def test_a_remission_guide_carries_what_an_invoice_sold(tmp_path):
    desk = a_configured_desk(tmp_path)
    invoice = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), TRANSFER).draft.identifier)
    desk.register_client("1104680135", name="Transportes Perez")
    recipient = Recipient(
        CLIENT_RUC,
        "CLIENTE S.A.S.",
        "AV. AMAZONAS N10-20, QUITO",
        "Venta",
        (GuideItem("Servidor", Decimal("1"), "SRV-1"),),
        supporting=desk.reference(invoice.voucher.number),
    )
    day = invoice.voucher.issued_on
    with pytest.raises(Refused):
        desk.draft_remission_guide(
            "1104680135", "LBA-1234", "LOJA", day, day - timedelta(days=1), (recipient,)
        )
    draft = desk.draft_remission_guide("1104680135", "LBA-1234", "LOJA", day, day, (recipient,))
    filed = desk.issue(draft.draft.identifier)
    assert filed.standing == Standing.AUTHORIZED and isinstance(filed.voucher, RemissionGuide)
    supporting = filed.voucher.recipients[0].supporting
    assert supporting is not None and supporting.authorization == invoice.voucher.access_key


def test_a_withholding_keeps_what_was_withheld_from_a_purchase(tmp_path):
    desk = a_configured_desk(tmp_path)
    purchase = SupportDocument(
        "01",
        Reference("01", "002-001-000000045", NOW.date(), None),
        (tax_on(Decimal("100"), "4"),),
        (withhold(RetentionTax.INCOME, "303", Decimal("100"), Decimal("10")),),
        (Payment(TRANSFER, Decimal("115.00")),),
    )
    with pytest.raises(Refused):
        desk.draft_withholding(CLIENT_RUC, NOW.date(), (replace(purchase, retentions=()),))
    filed = desk.issue(desk.draft_withholding(CLIENT_RUC, NOW.date(), (purchase,)).draft.identifier)
    assert filed.standing == Standing.AUTHORIZED and isinstance(filed.voucher, Withholding)
    assert filed.voucher.withheld == Decimal("10.00")


def test_a_draft_kept_by_version_1_1_still_issues(tmp_path):
    desk = a_configured_desk(tmp_path)
    kept = {
        "identifier": "0a1b2c3d",
        "kind": "invoice",
        "buyer": {"kind": "ruc", "identification": CLIENT_RUC, "name": "CLIENTE S.A.S."},
        "lines": [asdict(SERVICE)],
        "payment_form": "20",
        "modified": None,
        "created_at": NOW.isoformat(),
    }
    (tmp_path / "drafts").mkdir(exist_ok=True)
    (tmp_path / "drafts" / "0a1b2c3d.json").write_text(json.dumps(kept, default=str))
    filed = desk.issue("0a1b2c3d")
    assert filed.standing == Standing.AUTHORIZED and isinstance(filed.voucher, Voucher)
    assert filed.voucher.payments == (Payment(TRANSFER, Decimal("575.00")),)
