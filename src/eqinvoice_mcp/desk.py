from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from eqinvoice import (
    Buyer,
    BuyerKind,
    Credential,
    Environment,
    Line,
    Modified,
    Payment,
    Registry,
    SimulatedSri,
    Sri,
    SriUnavailable,
    Taxpayer,
    Voucher,
    VoucherKind,
    access_key,
    issue_date,
    read,
    sign_voucher,
    simulated_credential,
)
from eqinvoice.ride import Layout, render
from eqinvoice_mcp.book import Book
from eqinvoice_mcp.model import Draft, Filed, Previewed, Profile, Standing

RUC_DIGITS = 13
CEDULA_DIGITS = 10
DRAFT_ID_BYTES = 4
AUTHORIZATION_WAIT_SECONDS = 3.0


class Refused(ValueError):
    """The desk will not do what it was asked, and says why."""


def _now() -> datetime:
    return datetime.now(UTC)


def buyer_kind(identification: str) -> BuyerKind:
    if identification.isdigit() and len(identification) == RUC_DIGITS:
        return BuyerKind.RUC
    if identification.isdigit() and len(identification) == CEDULA_DIGITS:
        return BuyerKind.CEDULA
    return BuyerKind.PASSPORT


class Desk:
    def __init__(
        self,
        book: Book,
        registry: Registry,
        sri_for: Callable[[Environment], Sri],
        signature: Callable[[], Credential],
        clock: Callable[[], datetime] = _now,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        self._book = book
        self._registry = registry
        self._sri_for = sri_for
        self._signature = signature
        self._clock = clock
        self._wait = wait

    def look_up(self, ruc: str) -> Taxpayer:
        taxpayer = self._registry.find(ruc)
        if taxpayer is None:
            raise Refused(f"The SRI's registry holds no taxpayer with the RUC {ruc}")
        return taxpayer

    def _active(self, ruc: str) -> Taxpayer:
        taxpayer = self.look_up(ruc)
        if not taxpayer.active:
            raise Refused(f"{ruc} ({taxpayer.legal_name}) is not active in the SRI")
        return taxpayer

    def configure(
        self,
        ruc: str,
        environment: Environment,
        establishment: str,
        point: str,
        withholding_agent: str | None = None,
        special_taxpayer: str | None = None,
        system_provider_ruc: str | None = None,
    ) -> Profile:
        """The establishment's trade name is kept unless it only repeats the legal name."""
        taxpayer = self._active(ruc)
        office = taxpayer.establishment(establishment)
        if office is None or not office.open:
            raise Refused(f"{ruc} has no open establishment {establishment}")
        issuer = taxpayer.issuer(
            trade_name=office.trade_name if office.trade_name != taxpayer.legal_name else None,
            withholding_agent=withholding_agent,
            special_taxpayer=special_taxpayer,
            system_provider_ruc=system_provider_ruc,
        )
        profile = Profile(issuer, environment, establishment, point, office.address)
        self._book.keep_profile(profile)
        return profile

    def profile(self) -> Profile:
        return self._book.profile()

    def register_client(
        self, identification: str, email: str | None = None, name: str | None = None
    ) -> Buyer:
        """A client named by the SRI's registry when it has a RUC, otherwise by the name given."""
        kind = buyer_kind(identification)
        if kind == BuyerKind.RUC:
            name = self._active(identification).legal_name
        elif not name:
            raise Refused("Only a RUC is looked up in the SRI: a cédula or passport needs its name")
        buyer = Buyer(kind, identification, name, email)
        self._book.keep_client(buyer)
        return buyer

    def clients(self) -> tuple[Buyer, ...]:
        return self._book.clients()

    def forget_client(self, identification: str) -> tuple[Buyer, ...]:
        self._book.forget_client(identification)
        return self._book.clients()

    def _voucher(self, draft: Draft, profile: Profile, sequential: int, now: datetime) -> Voucher:
        issued_on = issue_date(now)
        voucher = Voucher(
            kind=draft.kind,
            environment=profile.environment,
            access_key=access_key(
                issued_on,
                draft.kind,
                profile.issuer.ruc,
                profile.environment,
                profile.establishment,
                profile.point,
                sequential,
            ),
            establishment=profile.establishment,
            point=profile.point,
            sequential=sequential,
            establishment_address=profile.establishment_address,
            issued_on=issued_on,
            buyer=draft.buyer,
            lines=draft.lines,
            modified=draft.modified,
        )
        if draft.payment_form is None:
            return voucher
        return replace(voucher, payments=(Payment(draft.payment_form, voucher.total),))

    def _upcoming(self, draft: Draft, profile: Profile, now: datetime) -> Voucher:
        sequential = self._book.next_sequential(
            profile.environment, draft.kind, profile.establishment, profile.point
        )
        return self._voucher(draft, profile, sequential, now)

    def _printed(self, voucher: Voucher, profile: Profile, now: datetime) -> bytes:
        """The voucher signed and authorized in simulation, and printed with the issuer's logo."""
        sri = SimulatedSri(lambda: now)
        sri.send(sign_voucher(voucher, profile.issuer, simulated_credential(), now))
        answer = sri.authorize(voucher.access_key)
        if answer is None:
            raise Refused("The simulated SRI did not authorize the preview")
        return render(read(answer.record), Layout.PAGE, logo=self._book.logo())

    def _draft(
        self,
        kind: VoucherKind,
        buyer: Buyer,
        lines: Sequence[Line],
        payment_form: str | None,
        modified: Modified | None,
    ) -> Previewed:
        if not lines:
            raise Refused("A voucher carries at least one line")
        profile = self._book.profile()
        now = self._clock()
        draft = Draft(
            identifier=secrets.token_hex(DRAFT_ID_BYTES),
            kind=kind,
            buyer=buyer,
            lines=tuple(lines),
            payment_form=payment_form,
            modified=modified,
            created_at=now,
        )
        voucher = self._upcoming(draft, profile, now)
        self._book.keep_draft(draft, self._printed(voucher, profile, now))
        return Previewed(draft, voucher, self._book.preview(draft.identifier))

    def draft_invoice(self, client: str, lines: Sequence[Line], payment_form: str) -> Previewed:
        buyer = self._book.client(client)
        return self._draft(VoucherKind.INVOICE, buyer, lines, payment_form, None)

    def draft_credit_note(
        self, invoice_number: str, reason: str, lines: Sequence[Line] = ()
    ) -> Previewed:
        """A credit note against an authorized invoice: all of it unless other lines are given."""
        environment = self._book.profile().environment
        filed = self._book.filed(environment, VoucherKind.INVOICE, invoice_number)
        invoice = filed.voucher
        if filed.standing != Standing.AUTHORIZED:
            raise Refused(f"The invoice {invoice_number} is not authorized")
        credited = replace(invoice, lines=tuple(lines) or invoice.lines)
        if credited.total > invoice.total:
            raise Refused(
                f"The credit note ({credited.total}) exceeds the invoice ({invoice.total})"
            )
        modified = Modified(invoice.number, invoice.issued_on, reason)
        return self._draft(VoucherKind.CREDIT_NOTE, invoice.buyer, credited.lines, None, modified)

    def drafts(self) -> tuple[Previewed, ...]:
        profile = self._book.profile()
        now = self._clock()
        return tuple(
            Previewed(
                draft, self._upcoming(draft, profile, now), self._book.preview(draft.identifier)
            )
            for draft in self._book.drafts()
        )

    def discard(self, identifier: str) -> tuple[Previewed, ...]:
        self._book.discard_draft(identifier)
        return self.drafts()

    def issue(self, identifier: str) -> Filed:
        draft = self._book.draft(identifier)
        profile = self._book.profile()
        credential = self._signature()
        now = self._clock()
        sequential = self._book.reserve(
            profile.environment, draft.kind, profile.establishment, profile.point
        )
        voucher = self._voucher(draft, profile, sequential, now)
        try:
            document = sign_voucher(voucher, profile.issuer, credential, now)
        except Exception:
            self._book.release(voucher)
            raise
        self._book.keep_signed(voucher, document)
        self._book.discard_draft(identifier)
        return self._deliver(voucher, document)

    def _filed(self, voucher: Voucher) -> Filed:
        return self._book.filed(voucher.environment, voucher.kind, voucher.number)

    def _deliver(self, voucher: Voucher, document: bytes) -> Filed:
        sri = self._sri_for(voucher.environment)
        try:
            reception = sri.send(document)
            if not reception.received and not reception.already_there:
                self._book.keep_refused(voucher, reception.messages)
                return self._filed(voucher)
            self._wait(AUTHORIZATION_WAIT_SECONDS)
            answer = sri.authorize(voucher.access_key)
        except SriUnavailable as unavailable:
            raise SriUnavailable(
                f"{voucher.number} is filed as pending; check it again later: {unavailable}"
            ) from unavailable
        if answer is None:
            return self._filed(voucher)
        if answer.authorized:
            printed = render(read(answer.record), Layout.PAGE, logo=self._book.logo())
            self._book.keep_authorized(voucher, answer.record, printed)
        else:
            self._book.keep_refused(voucher, answer.messages)
        return self._filed(voucher)

    def vouchers(self) -> tuple[Filed, ...]:
        return self._book.vouchers(self._book.profile().environment)

    def check(self, number: str, kind: VoucherKind = VoucherKind.INVOICE) -> Filed:
        """A pending voucher sent again and asked for; any other comes back as it stands."""
        filed = self._book.filed(self._book.profile().environment, kind, number)
        if filed.standing != Standing.PENDING:
            return filed
        return self._deliver(filed.voucher, filed.signed.read_bytes())
