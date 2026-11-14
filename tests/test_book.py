from __future__ import annotations

import shutil

from ecuador_einvoicing import Environment, VoucherKind
from ecuador_einvoicing.ride import ImageLogo, MarkLogo
from ecuador_einvoicing_mcp.book import Book
from tests.builders import FIXTURES, a_voucher


def test_each_kind_point_and_environment_numbers_its_own_series(tmp_path):
    book = Book(tmp_path)
    invoice, note = VoucherKind.INVOICE, VoucherKind.CREDIT_NOTE
    assert book.reserve(Environment.TEST, invoice, "001", "100") == 1
    assert book.reserve(Environment.TEST, invoice, "001", "100") == 2
    assert book.reserve(Environment.TEST, note, "001", "100") == 1
    assert book.reserve(Environment.TEST, invoice, "001", "101") == 1
    assert book.reserve(Environment.PRODUCTION, invoice, "001", "100") == 1


def test_a_released_number_comes_back(tmp_path):
    book = Book(tmp_path)
    book.reserve(Environment.TEST, VoucherKind.INVOICE, "001", "100")
    book.reserve(Environment.TEST, VoucherKind.INVOICE, "001", "100")
    book.release(a_voucher(sequential=2, point="100"))
    assert book.next_sequential(Environment.TEST, VoucherKind.INVOICE, "001", "100") == 2


def test_the_brand_folder_gives_the_logo(tmp_path):
    book = Book(tmp_path)
    brand = tmp_path / "brand"
    assert book.logo() is None
    brand.mkdir()
    shutil.copy(FIXTURES / "mark.svg", brand / "logo.png")
    assert isinstance(book.logo(), ImageLogo)
    for name in ("mark.svg", "name.svg", "PlusJakartaSans-ExtraBold.ttf"):
        shutil.copy(FIXTURES / name, brand / name)
    assert isinstance(book.logo(), MarkLogo)
