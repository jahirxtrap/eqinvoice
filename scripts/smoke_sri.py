from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from eqinvoice import (
    Buyer,
    BuyerKind,
    Environment,
    Issuer,
    Line,
    Payment,
    PaymentForm,
    SriWebServices,
    Voucher,
    VoucherKind,
    access_key,
    issue_date,
    read,
    read_credential,
    sign_voucher,
)
from eqinvoice.ride import Layout, render

USAGE = "usage: smoke_sri.py <setup.json> <out dir>, with EQINVOICE_P12 and EQINVOICE_P12_PASSWORD"
ATTEMPTS = 5
WAIT_SECONDS = 3
SEQUENTIAL_DIGITS = 9
FAILED = 1
WRONG_USE = 2


def _voucher(setup: dict[str, str], issuer: Issuer, now: datetime) -> Voucher:
    sequential = int(now.timestamp()) % 10**SEQUENTIAL_DIGITS
    issued_on = issue_date(now)
    establishment, point = setup["establishment"], setup["point"]
    return Voucher(
        kind=VoucherKind.INVOICE,
        environment=Environment.TEST,
        access_key=access_key(
            issued_on,
            VoucherKind.INVOICE,
            issuer.ruc,
            Environment.TEST,
            establishment,
            point,
            sequential,
        ),
        establishment=establishment,
        point=point,
        sequential=sequential,
        establishment_address=setup["establishment_address"],
        issued_on=issued_on,
        buyer=Buyer(BuyerKind.FINAL_CONSUMER),
        lines=(
            Line(
                code="SMOKE-1",
                description="Prueba de facturacion electronica",
                quantity=Decimal("1"),
                unit_price=Decimal("1.00"),
                discount=Decimal("0"),
                subtotal=Decimal("1.00"),
                vat=Decimal("0.15"),
                rate_code="4",
                rate_percentage=Decimal("15.00"),
            ),
        ),
        payments=(Payment(PaymentForm.WITHOUT_FINANCIAL_SYSTEM, Decimal("1.15")),),
    )


def main() -> int:
    if len(sys.argv) != 3 or "EQINVOICE_P12" not in os.environ:
        print(USAGE)
        return WRONG_USE
    setup = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    issuer = Issuer(**setup["issuer"])
    credential = read_credential(
        Path(os.environ["EQINVOICE_P12"]), os.environ.get("EQINVOICE_P12_PASSWORD", "")
    )
    now = datetime.now(UTC)
    voucher = _voucher(setup, issuer, now)
    document = sign_voucher(voucher, issuer, credential, now)
    sri = SriWebServices(Environment.TEST)
    reception = sri.send(document)
    print(f"sent {voucher.number}: received={reception.received}")
    if not reception.received and not reception.already_there:
        for message in reception.messages:
            print(f"  {message.identifier} {message.text} {message.detail}")
        return FAILED
    answer = None
    for _ in range(ATTEMPTS):
        answer = sri.authorize(voucher.access_key)
        if answer is not None:
            break
        time.sleep(WAIT_SECONDS)
    if answer is None or not answer.authorized:
        for message in answer.messages if answer else ():
            print(f"  {message.identifier} {message.text} {message.detail}")
        print("not authorized")
        return FAILED
    issued = read(answer.record)
    out.mkdir(parents=True, exist_ok=True)
    for layout in Layout:
        (out / f"{voucher.number}-{layout}.pdf").write_bytes(render(issued, layout))
    print(f"authorized at {issued.authorized_at:%Y-%m-%d %H:%M:%S} for {issued.total}")
    print(f"printed copies in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
