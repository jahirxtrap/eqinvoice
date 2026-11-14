from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BOOK = "EINVOICING_BOOK"
SIGNATURE = "EINVOICING_P12"
PASSWORD = "EINVOICING_P12_PASSWORD"


class SettingsMissing(RuntimeError):
    """The environment does not say where the book or the signature is."""


@dataclass(frozen=True)
class Settings:
    book: Path
    signature: Path
    password: str


def read_settings(variables: Mapping[str, str] = os.environ) -> Settings:
    missing = [name for name in (BOOK, SIGNATURE, PASSWORD) if not variables.get(name)]
    if missing:
        raise SettingsMissing(f"The desk needs {', '.join(missing)} in its environment")
    return Settings(Path(variables[BOOK]), Path(variables[SIGNATURE]), variables[PASSWORD])
