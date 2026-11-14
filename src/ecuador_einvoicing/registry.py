from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ecuador_einvoicing.errors import SriUnavailable, VoucherInvalid
from ecuador_einvoicing.model import Issuer
from ecuador_einvoicing.protocol import (
    ACTIVE_TAXPAYER,
    ESTABLISHMENTS_PATH,
    OPEN_ESTABLISHMENT,
    REGISTRY_HEADERS,
    REGISTRY_HOST,
    RIMPE_REGIME,
    TAXPAYER_PATH,
    YES,
)
from ecuador_einvoicing.service import TIMEOUT_SECONDS

NO_CONTENT = 204
CLIENT_FAILURE = 400


@dataclass(frozen=True)
class Establishment:
    number: str
    address: str
    trade_name: str | None
    head_office: bool
    open: bool


@dataclass(frozen=True)
class Taxpayer:
    ruc: str
    legal_name: str
    active: bool
    regime: str
    keeps_accounts: bool
    withholding_agent: bool
    special_taxpayer: bool
    activity: str
    establishments: tuple[Establishment, ...]

    def establishment(self, number: str) -> Establishment | None:
        return next((held for held in self.establishments if held.number == number), None)

    @property
    def head_office(self) -> Establishment | None:
        return next((held for held in self.establishments if held.head_office), None)

    def issuer(
        self,
        trade_name: str | None = None,
        withholding_agent: str | None = None,
        special_taxpayer: str | None = None,
        system_provider_ruc: str | None = None,
    ) -> Issuer:
        """The issuer its vouchers name; an agent or special taxpayer brings its number."""
        office = self.head_office
        if office is None:
            raise VoucherInvalid(f"The registry lists no head office for {self.ruc}")
        if self.withholding_agent and not withholding_agent:
            raise VoucherInvalid(f"{self.ruc} is a withholding agent: its resolution is required")
        if self.special_taxpayer and not special_taxpayer:
            raise VoucherInvalid(f"{self.ruc} is a special taxpayer: its number is required")
        return Issuer(
            legal_name=self.legal_name,
            ruc=self.ruc,
            head_office_address=office.address,
            keeps_accounts=self.keeps_accounts,
            trade_name=trade_name,
            special_taxpayer=special_taxpayer,
            withholding_agent=withholding_agent,
            rimpe=self.regime == RIMPE_REGIME,
            system_provider_ruc=system_provider_ruc,
        )


class Registry(Protocol):
    def find(self, ruc: str) -> Taxpayer | None: ...


def _establishment(listed: dict[str, Any]) -> Establishment:
    return Establishment(
        number=str(listed.get("numeroEstablecimiento") or ""),
        address=str(listed.get("direccionCompleta") or ""),
        trade_name=listed.get("nombreFantasiaComercial") or None,
        head_office=listed.get("matriz") == YES,
        open=listed.get("estado") == OPEN_ESTABLISHMENT,
    )


def _taxpayer(held: dict[str, Any], listed: list[dict[str, Any]]) -> Taxpayer:
    return Taxpayer(
        ruc=str(held.get("numeroRuc") or ""),
        legal_name=str(held.get("razonSocial") or ""),
        active=held.get("estadoContribuyenteRuc") == ACTIVE_TAXPAYER,
        regime=str(held.get("regimen") or ""),
        keeps_accounts=held.get("obligadoLlevarContabilidad") == YES,
        withholding_agent=held.get("agenteRetencion") == YES,
        special_taxpayer=held.get("contribuyenteEspecial") == YES,
        activity=str(held.get("actividadEconomicaPrincipal") or ""),
        establishments=tuple(_establishment(each) for each in listed),
    )


class SriRegistry:
    def __init__(
        self, timeout: float = TIMEOUT_SECONDS, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(
            base_url=REGISTRY_HOST, headers=REGISTRY_HEADERS, timeout=timeout, transport=transport
        )

    def _get(self, path: str, query: dict[str, str]) -> list[dict[str, Any]]:
        try:
            response = self._client.get(path, params=query)
        except httpx.HTTPError as unreachable:
            raise SriUnavailable(str(unreachable)) from unreachable
        if response.status_code == NO_CONTENT:
            return []
        if response.status_code >= CLIENT_FAILURE:
            raise SriUnavailable(f"{response.status_code} from the SRI's registry")
        try:
            found = response.json()
        except ValueError as unreadable:
            raise SriUnavailable(
                f"the SRI's registry answered something that is not JSON: {unreadable}"
            ) from None
        if not isinstance(found, list):
            raise SriUnavailable("the SRI's registry answered something that is not a list")
        return found

    def find(self, ruc: str) -> Taxpayer | None:
        held = self._get(TAXPAYER_PATH, {"ruc": ruc})
        if not held:
            return None
        return _taxpayer(held[0], self._get(ESTABLISHMENTS_PATH, {"numeroRuc": ruc}))


class SimulatedRegistry:
    def __init__(self, *taxpayers: Taxpayer) -> None:
        self._held = {taxpayer.ruc: taxpayer for taxpayer in taxpayers}

    def find(self, ruc: str) -> Taxpayer | None:
        return self._held.get(ruc)
