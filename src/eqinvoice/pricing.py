from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from eqinvoice.errors import VoucherInvalid
from eqinvoice.model import ZERO, Line, Retention, RetentionTax, TaxGroup
from eqinvoice.protocol import VAT_RATES, VAT_RETENTION_RATES

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, ROUND_HALF_UP)


def _share(base: Decimal, percentage: Decimal) -> Decimal:
    return cents(base * percentage / HUNDRED)


def _vat_rate(rate_code: str) -> Decimal:
    rate = VAT_RATES.get(rate_code)
    if rate is None:
        raise VoucherInvalid(f"The SRI has no IVA rate with the code {rate_code!r}")
    return rate


def price_line(
    code: str,
    description: str,
    quantity: Decimal,
    unit_price: Decimal,
    rate_code: str,
    discount: Decimal = ZERO,
) -> Line:
    rate = _vat_rate(rate_code)
    subtotal = cents(quantity * unit_price - discount)
    return Line(
        code=code,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        discount=discount,
        subtotal=subtotal,
        vat=_share(subtotal, rate),
        rate_code=rate_code,
        rate_percentage=rate,
    )


def tax_on(base: Decimal, rate_code: str) -> TaxGroup:
    rate = _vat_rate(rate_code)
    return TaxGroup(rate_code, cents(base), _share(base, rate), rate)


def withhold(
    tax: RetentionTax, code: str, base: Decimal, percentage: Decimal | None = None
) -> Retention:
    """An IVA retention takes its percentage from the SRI's code; income and ISD bring theirs."""
    if tax == RetentionTax.VAT:
        listed = VAT_RETENTION_RATES.get(code)
        if listed is None or percentage not in (None, listed):
            raise VoucherInvalid(f"{code} is no IVA retention of {percentage}% in the SRI's table")
        percentage = listed
    if percentage is None:
        raise VoucherInvalid(f"An {tax.name.lower()} retention needs its percentage")
    return Retention(tax, code, cents(base), percentage, _share(base, percentage))
