from __future__ import annotations

from datetime import UTC, date, datetime

from eqinvoice import (
    Environment,
    VoucherKind,
    access_key,
    check_digit,
    issue_date,
    voucher_number,
)
from tests.builders import RUC

SRI_SAMPLE_KEY = "0503201201176001321000110010030009900641234567814"


def test_a_number_is_written_as_the_sri_prints_it():
    assert voucher_number("001", "002", 15) == "001-002-000000015"


def test_the_check_digit_is_the_one_the_sri_prints_in_its_own_example():
    assert check_digit(SRI_SAMPLE_KEY[:48]) == SRI_SAMPLE_KEY[48]


def test_it_is_forty_nine_digits_that_say_what_the_voucher_is():
    key = access_key(
        date(2026, 10, 27), VoucherKind.INVOICE, RUC, Environment.PRODUCTION, "002", "001", 123
    )
    assert len(key) == 49 and key.isdigit()
    assert key[:8] == "27102026" and key[8:10] == "01" and key[10:23] == RUC
    assert key[23] == "2" and key[24:30] == "002001" and key[30:39] == "000000123"
    assert key[47] == "1" and check_digit(key[:48]) == key[48]


def test_a_credit_note_in_the_test_environment_is_coded_04_and_1():
    key = access_key(
        date(2026, 10, 27), VoucherKind.CREDIT_NOTE, RUC, Environment.TEST, "001", "001", 1
    )
    assert key[8:10] == "04" and key[23] == "1"


def test_the_day_is_the_one_on_the_sris_clock():
    assert issue_date(datetime(2026, 11, 7, 3, 0, tzinfo=UTC)) == date(2026, 11, 6)
