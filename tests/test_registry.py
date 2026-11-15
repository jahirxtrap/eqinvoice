from __future__ import annotations

from dataclasses import replace
from importlib import metadata

import httpx
import pytest

from eqinvoice import SriRegistry, SriUnavailable, VoucherInvalid
from eqinvoice.protocol import ESTABLISHMENTS_PATH, TAXPAYER_PATH
from tests.builders import RUC, a_taxpayer

HELD = [
    {
        "numeroRuc": RUC,
        "razonSocial": "ACME COMERCIAL S.A.S.",
        "estadoContribuyenteRuc": "ACTIVO",
        "actividadEconomicaPrincipal": "VENTA AL POR MAYOR DE COMPUTADORAS",
        "tipoContribuyente": "SOCIEDAD",
        "regimen": "GENERAL",
        "categoria": None,
        "obligadoLlevarContabilidad": "SI",
        "agenteRetencion": "NO",
        "contribuyenteEspecial": "NO",
    }
]
LISTED = [
    {
        "nombreFantasiaComercial": "ACME",
        "tipoEstablecimiento": "MAT",
        "direccionCompleta": "LOJA / LOJA / SUCRE / AV. UNIVERSITARIA 10-20",
        "estado": "ABIERTO",
        "numeroEstablecimiento": "001",
        "matriz": "SI",
    }
]


def registry(
    respond: httpx.MockTransport | None = None, asked: list[httpx.Request] | None = None
) -> SriRegistry:
    def answer(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append(request)
        if request.url.path == TAXPAYER_PATH:
            return httpx.Response(200, json=HELD)
        if request.url.path == ESTABLISHMENTS_PATH:
            return httpx.Response(200, json=LISTED)
        return httpx.Response(404)

    return SriRegistry(transport=respond or httpx.MockTransport(answer))


def test_a_ruc_reads_as_the_taxpayer_with_its_establishments():
    asked: list[httpx.Request] = []
    taxpayer = registry(asked=asked).find(RUC)
    assert taxpayer is not None and taxpayer.active and taxpayer.keeps_accounts
    assert taxpayer.legal_name == "ACME COMERCIAL S.A.S." and not taxpayer.withholding_agent
    office = taxpayer.head_office
    assert office is not None and office.number == "001" and office.open
    assert asked[0].url.params["ruc"] == RUC
    assert asked[1].url.params["numeroRuc"] == RUC
    agent = f"eqinvoice/{metadata.version('eqinvoice')} (+https://github.com/jahirxtrap/eqinvoice)"
    assert {request.headers["User-Agent"] for request in asked} == {agent}


def test_a_popular_business_reads_its_category():
    popular = [{**HELD[0], "regimen": "RIMPE", "categoria": "NEGOCIO POPULAR"}]

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=popular if request.url.path == TAXPAYER_PATH else LISTED)

    taxpayer = registry(httpx.MockTransport(answer)).find(RUC)
    assert taxpayer is not None and taxpayer.category == "NEGOCIO POPULAR"
    assert taxpayer.issuer().popular_business
    assert registry().find(RUC) == replace(taxpayer, regime="GENERAL", category="")


def test_a_ruc_nobody_holds_is_none():
    empty = httpx.MockTransport(lambda request: httpx.Response(204))
    assert registry(empty).find(RUC) is None


@pytest.mark.parametrize(
    "response",
    [httpx.Response(503), httpx.Response(200, text="<html/>"), httpx.Response(200, json={})],
)
def test_a_registry_that_fails_or_mumbles_is_unavailable(response):
    with pytest.raises(SriUnavailable):
        registry(httpx.MockTransport(lambda request: response)).find(RUC)


def test_a_registry_that_cannot_be_reached_is_unavailable():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(SriUnavailable):
        registry(httpx.MockTransport(refuse)).find(RUC)


def test_the_taxpayer_issues_from_its_head_office():
    issuer = a_taxpayer().issuer()
    assert issuer.head_office_address == "LOJA / LOJA / SUCRE / AV. UNIVERSITARIA 10-20"
    assert issuer.keeps_accounts and not issuer.rimpe
    assert a_taxpayer(regime="RIMPE").issuer().rimpe
    popular = a_taxpayer(regime="RIMPE", category="NEGOCIO POPULAR").issuer()
    assert popular.rimpe and popular.popular_business
    assert not a_taxpayer(regime="RIMPE", category="EMPRENDEDOR").issuer().popular_business


def test_a_withholding_agent_or_special_taxpayer_brings_its_number():
    with pytest.raises(VoucherInvalid):
        a_taxpayer(withholding_agent=True).issuer()
    with pytest.raises(VoucherInvalid):
        a_taxpayer(special_taxpayer=True).issuer()
    issuer = a_taxpayer(withholding_agent=True).issuer(withholding_agent="1")
    assert issuer.withholding_agent == "1"
    special = a_taxpayer(special_taxpayer=True).issuer(special_taxpayer="5368")
    assert special.special_taxpayer == "5368"


def test_a_number_the_registry_does_not_back_is_refused():
    with pytest.raises(VoucherInvalid):
        a_taxpayer().issuer(withholding_agent="1")
    with pytest.raises(VoucherInvalid):
        a_taxpayer().issuer(special_taxpayer="5368")
