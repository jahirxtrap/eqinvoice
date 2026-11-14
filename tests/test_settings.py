from __future__ import annotations

from pathlib import Path

import pytest

from ecuador_einvoicing_mcp.settings import SettingsMissing, read_settings

COMPLETE = {
    "EINVOICING_BOOK": "/book",
    "EINVOICING_P12": "/signature.p12",
    "EINVOICING_P12_PASSWORD": "secret",
}


def test_the_settings_name_whatever_is_missing():
    with pytest.raises(SettingsMissing, match="EINVOICING_P12_PASSWORD"):
        read_settings({**COMPLETE, "EINVOICING_P12_PASSWORD": ""})


def test_complete_settings_say_where_the_book_and_the_signature_are():
    settings = read_settings(COMPLETE)
    assert settings.book == Path("/book") and settings.signature == Path("/signature.p12")
    assert settings.password == "secret"
