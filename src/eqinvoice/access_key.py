from __future__ import annotations

import secrets
from datetime import date, datetime

from eqinvoice.model import Environment, VoucherKind, sequential_code
from eqinvoice.protocol import (
    CLOCK,
    DOCUMENT_CODES,
    ENVIRONMENT_CODES,
    NORMAL_EMISSION,
)

WEIGHTS = (2, 3, 4, 5, 6, 7)
MODULUS = 11
NUMERIC_CODE_DIGITS = 8


def check_digit(digits: str) -> str:
    total = sum(
        int(digit) * WEIGHTS[index % len(WEIGHTS)] for index, digit in enumerate(reversed(digits))
    )
    remainder = MODULUS - total % MODULUS
    return {MODULUS: "0", MODULUS - 1: "1"}.get(remainder, str(remainder))


def access_key(
    issued_on: date,
    kind: VoucherKind,
    ruc: str,
    environment: Environment,
    establishment: str,
    point: str,
    sequential: int,
) -> str:
    """The clave de acceso, with a fresh numeric code and its check digit."""
    numeric = f"{secrets.randbelow(10**NUMERIC_CODE_DIGITS):0{NUMERIC_CODE_DIGITS}d}"
    body = (
        f"{issued_on:%d%m%Y}{DOCUMENT_CODES[kind]}{ruc}{ENVIRONMENT_CODES[environment]}"
        f"{establishment}{point}{sequential_code(sequential)}{numeric}{NORMAL_EMISSION}"
    )
    return body + check_digit(body)


def issue_date(now: datetime) -> date:
    return now.astimezone(CLOCK).date()
