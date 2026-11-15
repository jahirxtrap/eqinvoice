from __future__ import annotations

from enum import StrEnum


class SriUnavailable(RuntimeError):
    """The SRI could not be reached or failed on its side; the same voucher can be sent again."""


class VoucherInvalid(ValueError):
    """The voucher does not satisfy the SRI's schema, so sending it would only be returned."""


class CredentialProblem(StrEnum):
    MISSING = "missing"
    UNREADABLE = "unreadable"
    OUT_OF_DATE = "out_of_date"
    NOT_FOR_SIGNING = "not_for_signing"


class CredentialRefused(RuntimeError):
    def __init__(self, problem: CredentialProblem, reason: str) -> None:
        super().__init__(reason)
        self.problem = problem
