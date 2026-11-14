from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ecuador_einvoicing import (
    Authorization,
    BuyerKind,
    CredentialRefused,
    Environment,
    Message,
    Payment,
    Reception,
    SimulatedRegistry,
    SimulatedSri,
    Sri,
    SriUnavailable,
    VoucherKind,
    price_line,
    simulated_credential,
)
from ecuador_einvoicing_mcp.book import Book, NotInBook
from ecuador_einvoicing_mcp.desk import Desk, Refused
from ecuador_einvoicing_mcp.model import Standing
from tests.builders import NOW, RUC, a_taxpayer

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


def a_desk(root: Path, sri: Sri | None = None, clock=lambda: NOW) -> Desk:
    taxpayers = (a_taxpayer(), a_taxpayer(ruc=CLIENT_RUC, legal_name="CLIENTE S.A.S."))
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
    previewed = a_configured_desk(tmp_path).draft_invoice(CLIENT_RUC, (SERVICE,), "20")
    voucher = previewed.voucher
    assert voucher.number == "001-100-000000001" and voucher.total == Decimal("575.00")
    assert voucher.payments == (Payment("20", Decimal("575.00")),)
    assert voucher.buyer.email == "pagos@cliente.ec"
    assert previewed.preview.read_bytes().startswith(b"%PDF")


def test_a_draft_needs_a_line_and_a_client_in_the_book(tmp_path):
    desk = a_configured_desk(tmp_path)
    with pytest.raises(Refused):
        desk.draft_invoice(CLIENT_RUC, (), "20")
    with pytest.raises(NotInBook):
        desk.draft_invoice("0999999999", (SERVICE,), "20")


def test_issuing_numbers_signs_and_files_each_voucher_once_authorized(tmp_path):
    desk = a_configured_desk(tmp_path)
    first = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), "20").draft.identifier)
    second = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), "20").draft.identifier)
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
    filed = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), "20").draft.identifier)
    assert filed.standing == Standing.REFUSED and filed.messages == (RETURNED,)
    assert desk.check(filed.voucher.number) == filed


def test_an_unreachable_sri_leaves_the_voucher_pending_until_it_is_checked(tmp_path):
    desk = a_configured_desk(tmp_path, Unreachable())
    draft = desk.draft_invoice(CLIENT_RUC, (SERVICE,), "20").draft
    with pytest.raises(SriUnavailable, match="001-100-000000001 is filed as pending"):
        desk.issue(draft.identifier)
    assert desk.vouchers()[0].standing == Standing.PENDING
    checked = a_desk(tmp_path).check("001-100-000000001")
    assert checked.standing == Standing.AUTHORIZED and checked.printed is not None


def test_a_signature_that_cannot_sign_gives_its_number_back(tmp_path):
    a_configured_desk(tmp_path)
    draft = a_desk(tmp_path).draft_invoice(CLIENT_RUC, (SERVICE,), "20").draft
    late = a_desk(tmp_path, clock=lambda: NOW + timedelta(days=365 * 20))
    with pytest.raises(CredentialRefused):
        late.issue(draft.identifier)
    assert a_desk(tmp_path).issue(draft.identifier).voucher.number == "001-100-000000001"


def test_a_credit_note_offsets_an_authorized_invoice_and_never_exceeds_it(tmp_path):
    desk = a_configured_desk(tmp_path)
    invoice = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), "20").draft.identifier)
    number = invoice.voucher.number
    whole = desk.draft_credit_note(number, "Devolucion")
    assert whole.voucher.kind == VoucherKind.CREDIT_NOTE and whole.voucher.payments == ()
    assert whole.voucher.modified is not None and whole.voucher.modified.number == number
    assert whole.voucher.total == invoice.voucher.total
    with pytest.raises(Refused):
        desk.draft_credit_note(number, "Devolucion", (replace(SERVICE, subtotal=Decimal("600")),))
    note = desk.issue(whole.draft.identifier)
    assert note.standing == Standing.AUTHORIZED and note.voucher.number == "001-100-000000001"
    assert desk.check(note.voucher.number, VoucherKind.CREDIT_NOTE) == note
    with pytest.raises(NotInBook):
        desk.draft_credit_note("001-100-000000009", "Devolucion")


def test_a_credit_note_needs_an_invoice_the_sri_authorized(tmp_path):
    desk = a_configured_desk(tmp_path, Returning())
    refused = desk.issue(desk.draft_invoice(CLIENT_RUC, (SERVICE,), "20").draft.identifier)
    with pytest.raises(Refused):
        desk.draft_credit_note(refused.voucher.number, "Devolucion")
