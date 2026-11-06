from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

LABELS_DIR = Path(__file__).resolve().parent.parent / "labels"
DEFAULT_LANGUAGE = "es"

Labels = dict[str, Any]


def read_labels(path: Path) -> Labels:
    """A labels file of the same shape as the default one."""
    return dict(json.loads(path.read_text(encoding="utf-8")))


@cache
def default_labels() -> Labels:
    return read_labels(LABELS_DIR / f"{DEFAULT_LANGUAGE}.json")


def filled(template: str, **values: str) -> str:
    """A label with its {{placeholders}} filled."""
    for name, value in values.items():
        template = template.replace(f"{{{{{name}}}}}", value)
    return template
