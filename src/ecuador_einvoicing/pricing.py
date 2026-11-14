from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ecuador_einvoicing.errors import VoucherInvalid
from ecuador_einvoicing.model import ZERO, Line
from ecuador_einvoicing.protocol import VAT_RATES

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, ROUND_HALF_UP)


def price_line(
    code: str,
    description: str,
    quantity: Decimal,
    unit_price: Decimal,
    rate_code: str,
    discount: Decimal = ZERO,
) -> Line:
    rate = VAT_RATES.get(rate_code)
    if rate is None:
        raise VoucherInvalid(f"The SRI has no IVA rate with the code {rate_code!r}")
    subtotal = cents(quantity * unit_price - discount)
    return Line(
        code=code,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        discount=discount,
        subtotal=subtotal,
        vat=cents(subtotal * rate / HUNDRED),
        rate_code=rate_code,
        rate_percentage=rate,
    )
