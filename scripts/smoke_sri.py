from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

from eqinvoice import (
    AnyVoucher,
    Authorization,
    Buyer,
    BuyerKind,
    Environment,
    GuideItem,
    Issuer,
    Modified,
    Payment,
    PaymentForm,
    Recipient,
    Reference,
    RemissionGuide,
    RetentionTax,
    SriWebServices,
    SupportDocument,
    Voucher,
    VoucherKind,
    Withholding,
    access_key,
    batch,
    issue_date,
    price_line,
    read,
    read_credential,
    sign_voucher,
    tax_on,
    withhold,
)
from eqinvoice.ride import Layout, render

USAGE = (
    "usage: smoke_sri.py <setup.json> <out dir> [kind ... | lote], "
    "with EQINVOICE_P12 and EQINVOICE_P12_PASSWORD"
)
LOTE = "lote"
ATTEMPTS = 5
WAIT_SECONDS = 4
PAUSE_SECONDS = 6
SEQUENTIAL_DIGITS = 9
FAILED = 1
WRONG_USE = 2
TRANSFER = PaymentForm.WITH_FINANCIAL_SYSTEM
SERVICE = price_line(
    "SMOKE-1", "Prueba de facturacion electronica", Decimal("1"), Decimal("1"), "4"
)


def _party(held: dict[str, str]) -> Buyer:
    return Buyer(
        BuyerKind(held["kind"]), held["identification"], held["name"], None, held.get("address")
    )


def _sold(setup: dict[str, Any]) -> Reference:
    held = setup["invoice"]
    return Reference(
        "01", held["number"], date.fromisoformat(held["issued_on"]), held["access_key"]
    )


def _numbered(setup: dict[str, Any], kind: VoucherKind, sequential: int, now: datetime) -> dict:
    issued_on = issue_date(now)
    ruc, establishment, point = setup["issuer"]["ruc"], setup["establishment"], setup["point"]
    return {
        "kind": kind,
        "environment": Environment.TEST,
        "access_key": access_key(
            issued_on, kind, ruc, Environment.TEST, establishment, point, sequential
        ),
        "establishment": establishment,
        "point": point,
        "sequential": sequential,
        "establishment_address": setup["establishment_address"],
        "issued_on": issued_on,
    }


def _priced(numbered: dict, setup: dict[str, Any]) -> AnyVoucher:
    kind, sold = numbered["kind"], _sold(setup)
    paid = (Payment(TRANSFER, Decimal("1.15")),)
    modified = Modified(sold.number, sold.issued_on, "Prueba")
    shapes: dict[VoucherKind, dict[str, Any]] = {
        VoucherKind.INVOICE: {"buyer": Buyer(BuyerKind.FINAL_CONSUMER), "payments": paid},
        VoucherKind.CREDIT_NOTE: {"buyer": _party(setup["party"]), "modified": modified},
        VoucherKind.DEBIT_NOTE: {
            "buyer": _party(setup["party"]),
            "modified": modified,
            "payments": paid,
        },
        VoucherKind.PURCHASE_SETTLEMENT: {"buyer": _party(setup["supplier"]), "payments": paid},
    }
    return Voucher(**numbered, lines=(SERVICE,), **shapes[kind])


def _guide(numbered: dict, setup: dict[str, Any]) -> AnyVoucher:
    party = setup["party"]
    return RemissionGuide(
        **numbered,
        carrier=_party(setup["carrier"]),
        plate=setup["plate"],
        departure=setup["establishment_address"],
        starts_on=numbered["issued_on"],
        ends_on=numbered["issued_on"],
        recipients=(
            Recipient(
                party["identification"],
                party["name"],
                party["address"],
                "Prueba",
                (GuideItem("Prueba de facturacion electronica", Decimal("1"), "SMOKE-1"),),
                supporting=_sold(setup),
            ),
        ),
    )


def _withholding(numbered: dict, setup: dict[str, Any]) -> AnyVoucher:
    purchase = SupportDocument(
        support_code="01",
        document=Reference("01", setup["purchase"], numbered["issued_on"]),
        taxes=(tax_on(Decimal("1"), "4"),),
        retentions=(
            withhold(RetentionTax.INCOME, "303", Decimal("1"), Decimal("10")),
            withhold(RetentionTax.VAT, "1", Decimal("0.15")),
        ),
        payments=(Payment(TRANSFER, Decimal("1.15")),),
    )
    return Withholding(
        **numbered,
        subject=_party(setup["party"]),
        fiscal_period=numbered["issued_on"].replace(day=1),
        documents=(purchase,),
    )


BUILDERS: dict[VoucherKind, Callable[[dict, dict[str, Any]], AnyVoucher]] = {
    VoucherKind.INVOICE: _priced,
    VoucherKind.CREDIT_NOTE: _priced,
    VoucherKind.DEBIT_NOTE: _priced,
    VoucherKind.PURCHASE_SETTLEMENT: _priced,
    VoucherKind.REMISSION_GUIDE: _guide,
    VoucherKind.WITHHOLDING: _withholding,
}


def _asked(ask: Callable[[], Any]) -> Any:
    for _ in range(ATTEMPTS):
        time.sleep(WAIT_SECONDS)
        if answer := ask():
            return answer
    return None


def _report(voucher: AnyVoucher, answer: Authorization | None, out: Path) -> bool:
    if answer is None or not answer.authorized:
        print(f"  {voucher.kind} {voucher.number}: not authorized")
        for message in answer.messages if answer else ():
            print(f"    {message.identifier} {message.text} {message.detail}")
        return False
    issued = read(answer.record)
    for layout in Layout:
        (out / f"{voucher.kind}-{voucher.number}-{layout}.pdf").write_bytes(render(issued, layout))
    print(f"  {voucher.kind} {voucher.number}: authorized at {issued.authorized_at:%H:%M:%S}")
    return True


def main() -> int:
    if len(sys.argv) < 3 or "EQINVOICE_P12" not in os.environ:
        print(USAGE)
        return WRONG_USE
    setup = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    asked = sys.argv[3:] or [VoucherKind.INVOICE]
    issuer = Issuer(**setup["issuer"])
    credential = read_credential(
        Path(os.environ["EQINVOICE_P12"]), os.environ.get("EQINVOICE_P12_PASSWORD", "")
    )
    sri = SriWebServices(Environment.TEST)
    first = int(datetime.now(UTC).timestamp()) % 10**SEQUENTIAL_DIGITS
    authorized = True
    for index, name in enumerate(asked):
        now = datetime.now(UTC)
        sequential = first + index * 10
        if name == LOTE:
            vouchers = [
                _priced(_numbered(setup, VoucherKind.INVOICE, sequential + 1 + held, now), setup)
                for held in range(2)
            ]
            key = _numbered(setup, VoucherKind.INVOICE, sequential, now)["access_key"]
            documents = [sign_voucher(held, issuer, credential, now) for held in vouchers]
            reception = sri.send(batch(documents, issuer.ruc, key))
            print(f"lote of {len(vouchers)}: received={reception.received}")
            answers = _asked(partial(sri.authorize_batch, key)) or {}
            for held in vouchers:
                authorized &= _report(held, answers.get(held.access_key), out)
        else:
            kind = VoucherKind(name)
            voucher = BUILDERS[kind](_numbered(setup, kind, sequential, now), setup)
            reception = sri.send(sign_voucher(voucher, issuer, credential, now))
            print(f"sent {kind} {voucher.number}: received={reception.received}")
            if not reception.received and not reception.already_there:
                for message in reception.messages:
                    print(f"    {message.identifier} {message.text} {message.detail}")
                authorized = False
            else:
                authorized &= _report(
                    voucher, _asked(partial(sri.authorize, voucher.access_key)), out
                )
        time.sleep(PAUSE_SECONDS)
    print(f"printed copies in {out}")
    return 0 if authorized else FAILED


if __name__ == "__main__":
    sys.exit(main())
