from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import TypeVar

from pydantic import TypeAdapter

from eqinvoice import (
    AnyVoucher,
    Buyer,
    Environment,
    Message,
    VoucherKind,
    read_voucher,
    voucher_number,
)
from eqinvoice.ride import ImageLogo, Logo, MarkLogo
from eqinvoice_mcp.model import Draft, Filed, LegacyDraft, Lockup, Profile, Standing

PROFILE = "profile.json"
CLIENTS = "clients"
DRAFTS = "drafts"
VOUCHERS = "vouchers"
BRAND = "brand"
SIGNED = "signed.xml"
AUTHORIZED = "authorized.xml"
REFUSED = "refused.json"
PRINTED = "printed.pdf"
DATA = ".json"
PREVIEW = ".pdf"
MARK = "mark.svg"
NAME = "name.svg"
FONTS = "*.ttf"
SIZING = "lockup.json"
PICTURES = ("logo.png", "logo.jpg")
INDENT = 2

PROFILES = TypeAdapter(Profile)
LOCKUP = TypeAdapter(Lockup)
CLIENT = TypeAdapter(Buyer)
DRAFT: TypeAdapter[Draft | LegacyDraft] = TypeAdapter(Draft | LegacyDraft)
MESSAGES = TypeAdapter(tuple[Message, ...])

Held = TypeVar("Held")


class NotInBook(LookupError):
    """The book holds nothing under that name."""


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as written:
        written.write(content)
    os.replace(written.name, path)


def _load(path: Path, adapter: TypeAdapter[Held], missing: str) -> Held:
    if not path.is_file():
        raise NotInBook(missing)
    return adapter.validate_json(path.read_bytes())


def _current(held: Draft | LegacyDraft) -> Draft:
    return held.upgraded() if isinstance(held, LegacyDraft) else held


class Book:
    def __init__(self, root: Path) -> None:
        self.root = root

    def profile(self) -> Profile:
        return _load(
            self.root / PROFILE, PROFILES, "The book has no issuer yet: configure it first"
        )

    def keep_profile(self, profile: Profile) -> None:
        _write(self.root / PROFILE, PROFILES.dump_json(profile, indent=INDENT))

    def _client(self, identification: str) -> Path:
        return self.root / CLIENTS / f"{identification}{DATA}"

    def client(self, identification: str) -> Buyer:
        return _load(
            self._client(identification), CLIENT, f"No client {identification} in the book"
        )

    def clients(self) -> tuple[Buyer, ...]:
        return tuple(
            CLIENT.validate_json(path.read_bytes())
            for path in sorted((self.root / CLIENTS).glob(f"*{DATA}"))
        )

    def keep_client(self, buyer: Buyer) -> None:
        _write(self._client(str(buyer.identification)), CLIENT.dump_json(buyer, indent=INDENT))

    def forget_client(self, identification: str) -> None:
        path = self._client(identification)
        if not path.is_file():
            raise NotInBook(f"No client {identification} in the book")
        path.unlink()

    def _draft(self, identifier: str, suffix: str) -> Path:
        return self.root / DRAFTS / f"{identifier}{suffix}"

    def draft(self, identifier: str) -> Draft:
        return _current(
            _load(self._draft(identifier, DATA), DRAFT, f"No draft {identifier} in the book")
        )

    def drafts(self) -> tuple[Draft, ...]:
        held = (
            _current(DRAFT.validate_json(path.read_bytes()))
            for path in (self.root / DRAFTS).glob(f"*{DATA}")
        )
        return tuple(sorted(held, key=lambda draft: draft.created_at))

    def keep_draft(self, draft: Draft, preview: bytes) -> None:
        _write(self._draft(draft.identifier, PREVIEW), preview)
        _write(self._draft(draft.identifier, DATA), DRAFT.dump_json(draft, indent=INDENT))

    def preview(self, identifier: str) -> Path:
        return self._draft(identifier, PREVIEW)

    def discard_draft(self, identifier: str) -> None:
        held = self._draft(identifier, DATA)
        if not held.is_file():
            raise NotInBook(f"No draft {identifier} in the book")
        held.unlink()
        self._draft(identifier, PREVIEW).unlink(missing_ok=True)

    def _shelf(self, environment: Environment, kind: VoucherKind) -> Path:
        return self.root / VOUCHERS / environment.value / kind.value

    def _folder(self, voucher: AnyVoucher) -> Path:
        return self._shelf(voucher.environment, voucher.kind) / voucher.number

    def next_sequential(
        self, environment: Environment, kind: VoucherKind, establishment: str, point: str
    ) -> int:
        """The sequential after the highest one this kind, establishment and point took."""
        prefix = f"{establishment}-{point}-"
        taken = (
            int(folder.name.removeprefix(prefix))
            for folder in self._shelf(environment, kind).glob(f"{prefix}*")
            if folder.is_dir()
        )
        return max(taken, default=0) + 1

    def reserve(
        self, environment: Environment, kind: VoucherKind, establishment: str, point: str
    ) -> int:
        """The next sequential, taken for good by creating its folder."""
        sequential = self.next_sequential(environment, kind, establishment, point)
        folder = self._shelf(environment, kind) / voucher_number(establishment, point, sequential)
        folder.parent.mkdir(parents=True, exist_ok=True)
        try:
            folder.mkdir()
        except FileExistsError:
            return self.reserve(environment, kind, establishment, point)
        return sequential

    def release(self, voucher: AnyVoucher) -> None:
        self._folder(voucher).rmdir()

    def keep_signed(self, voucher: AnyVoucher, document: bytes) -> None:
        _write(self._folder(voucher) / SIGNED, document)

    def keep_authorized(self, voucher: AnyVoucher, record: str, printed: bytes) -> None:
        folder = self._folder(voucher)
        _write(folder / PRINTED, printed)
        _write(folder / AUTHORIZED, record.encode())

    def keep_refused(self, voucher: AnyVoucher, messages: tuple[Message, ...]) -> None:
        _write(self._folder(voucher) / REFUSED, MESSAGES.dump_json(messages, indent=INDENT))

    def filed(self, environment: Environment, kind: VoucherKind, number: str) -> Filed:
        folder = self._shelf(environment, kind) / number
        signed, authorized, refused, printed = (
            folder / name for name in (SIGNED, AUTHORIZED, REFUSED, PRINTED)
        )
        if not signed.is_file():
            raise NotInBook(f"No {kind.value} {number} in the {environment.value} environment")
        standing = (
            Standing.AUTHORIZED
            if authorized.is_file()
            else Standing.REFUSED
            if refused.is_file()
            else Standing.PENDING
        )
        return Filed(
            voucher=read_voucher(signed.read_bytes()),
            standing=standing,
            signed=signed,
            authorized=authorized if authorized.is_file() else None,
            printed=printed if printed.is_file() else None,
            messages=MESSAGES.validate_json(refused.read_bytes()) if refused.is_file() else (),
        )

    def vouchers(self, environment: Environment) -> tuple[Filed, ...]:
        return tuple(
            self.filed(environment, kind, folder.name)
            for kind in VoucherKind
            for folder in sorted(self._shelf(environment, kind).glob("*"))
            if (folder / SIGNED).is_file()
        )

    def logo(self) -> Logo | None:
        brand = self.root / BRAND
        mark, name, fonts = brand / MARK, brand / NAME, sorted(brand.glob(FONTS))
        if mark.is_file() and name.is_file() and fonts:
            sizing = brand / SIZING
            chosen = asdict(LOCKUP.validate_json(sizing.read_bytes())) if sizing.is_file() else {}
            given = {key: value for key, value in chosen.items() if value is not None}
            return MarkLogo(mark=mark, name=name, font=fonts[0], **given)
        picture = next((brand / each for each in PICTURES if (brand / each).is_file()), None)
        return ImageLogo(picture) if picture else None
