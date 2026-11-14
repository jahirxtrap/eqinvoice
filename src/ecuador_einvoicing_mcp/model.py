from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from ecuador_einvoicing import (
    Buyer,
    Environment,
    Issuer,
    Line,
    Message,
    Modified,
    Voucher,
    VoucherKind,
)


class Standing(StrEnum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    REFUSED = "refused"


@dataclass(frozen=True)
class Profile:
    issuer: Issuer
    environment: Environment
    establishment: str
    point: str
    establishment_address: str


@dataclass(frozen=True)
class Draft:
    identifier: str
    kind: VoucherKind
    buyer: Buyer
    lines: tuple[Line, ...]
    payment_form: str | None
    modified: Modified | None
    created_at: datetime


@dataclass(frozen=True)
class Previewed:
    draft: Draft
    voucher: Voucher
    preview: Path


@dataclass(frozen=True)
class Filed:
    voucher: Voucher
    standing: Standing
    signed: Path
    authorized: Path | None
    printed: Path | None
    messages: tuple[Message, ...]
