from __future__ import annotations

import json

import pytest
from PIL import Image as Picture
from reportlab import rl_config

from ecuador_einvoicing import VoucherKind
from ecuador_einvoicing.ride import ImageLogo, Layout, default_labels, read_labels, render
from tests.builders import LOGO, issued

STRIP = b"/MediaBox [ 0 0 226.7717"


@pytest.fixture
def plain(monkeypatch):
    monkeypatch.setattr(rl_config, "pageCompression", 0)


def test_a_page_and_a_strip_are_pdfs_the_strip_80_mm_wide():
    voucher = issued()
    page = render(voucher)
    strip = render(voucher, Layout.TICKET)
    assert page.startswith(b"%PDF") and strip.startswith(b"%PDF")
    assert STRIP in strip and STRIP not in page


def test_the_totals_follow_the_sris_block_with_the_tip_only_on_an_invoice(plain):
    page = render(issued())
    for label in (b"SUBTOTAL 15%", b"SUBTOTAL 0%", b"SUBTOTAL NO OBJETO DE IVA"):
        assert label in page
    for label in (b"SUBTOTAL EXENTO DE IVA", b"(ICE)", b"(IRBPNR)", b"(PROPINA)", b"13.30"):
        assert label in page
    assert b"(PROPINA)" not in render(issued(VoucherKind.CREDIT_NOTE))


def test_a_mark_logo_draws_its_name_in_its_own_typeface_on_both():
    voucher = issued()
    for layout in Layout:
        assert b"PlusJakartaSans-ExtraBold" in render(voucher, layout, logo=LOGO)


def test_a_picture_logo_is_placed_on_the_page_and_its_one_ink_copy_on_the_strip(tmp_path):
    colour, ink = tmp_path / "logo.png", tmp_path / "logo-ink.png"
    Picture.new("RGB", (240, 80), (15, 118, 110)).save(colour)
    Picture.new("L", (240, 80), 0).save(ink)
    logo = ImageLogo(colour, one_ink_path=ink)
    voucher = issued()
    assert b"/Subtype /Image" in render(voucher, logo=logo)
    assert b"/Subtype /Image" in render(voucher, Layout.TICKET, logo=logo)


def test_other_labels_replace_the_spanish_ones(plain, tmp_path):
    labels = {**default_labels(), "documents": {"invoice": "BOLETA", "credit_note": "ABONO"}}
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(labels), encoding="utf-8")
    assert b"(BOLETA)" in render(issued(), labels=read_labels(path))
