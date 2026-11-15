from __future__ import annotations

import base64

import httpx
import pytest
from lxml import etree

from eqinvoice import (
    Environment,
    SimulatedSri,
    SriUnavailable,
    SriWebServices,
    Voucher,
    VoucherInvalid,
    VoucherKind,
    access_key,
    batch,
)
from eqinvoice.protocol import AUTHORIZATION_PATH, RECEPTION_PATH, USER_AGENT
from tests.builders import ISSUED_ON, RUC, a_voucher, signed

RECEIVED = (
    b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
    b'<ns2:validarComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.recepcion">'
    b"<RespuestaRecepcionComprobante><estado>RECIBIDA</estado><comprobantes/>"
    b"</RespuestaRecepcionComprobante></ns2:validarComprobanteResponse></soap:Body></soap:Envelope>"
)
RETURNED = (
    b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
    b'<ns2:validarComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.recepcion">'
    b"<RespuestaRecepcionComprobante><estado>DEVUELTA</estado><comprobantes><comprobante>"
    b"<claveAcceso>N/A</claveAcceso><mensajes><mensaje><identificador>43</identificador>"
    b"<mensaje>CLAVE ACCESO REGISTRADA</mensaje><informacionAdicional>ya recibida"
    b"</informacionAdicional><tipo>ERROR</tipo></mensaje></mensajes></comprobante></comprobantes>"
    b"</RespuestaRecepcionComprobante></ns2:validarComprobanteResponse></soap:Body></soap:Envelope>"
)
NOT_AUTHORIZED = (
    b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
    b'<ns2:autorizacionComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.autorizacion">'
    b"<RespuestaAutorizacionComprobante><claveAccesoConsultada>1</claveAccesoConsultada>"
    b"<numeroComprobantes>1</numeroComprobantes><autorizaciones><autorizacion>"
    b"<estado>NO AUTORIZADO</estado><fechaAutorizacion>2026-10-27T12:55:15-05:00"
    b"</fechaAutorizacion><ambiente>PRUEBAS</ambiente><comprobante>&lt;factura/&gt;</comprobante>"
    b"<mensajes><mensaje><identificador>39</identificador><mensaje>FIRMA INVALIDA</mensaje>"
    b"<informacionAdicional>cadena de confianza</informacionAdicional><tipo>ERROR</tipo>"
    b"</mensaje></mensajes></autorizacion></autorizaciones></RespuestaAutorizacionComprobante>"
    b"</ns2:autorizacionComprobanteResponse></soap:Body></soap:Envelope>"
)
PROCESSING = (
    b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
    b'<ns2:autorizacionComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.autorizacion">'
    b"<RespuestaAutorizacionComprobante><claveAccesoConsultada>1</claveAccesoConsultada>"
    b"<numeroComprobantes>0</numeroComprobantes><autorizaciones/>"
    b"</RespuestaAutorizacionComprobante></ns2:autorizacionComprobanteResponse>"
    b"</soap:Body></soap:Envelope>"
)


def answering(body: bytes, status: int = 200, asked: list[httpx.Request] | None = None):
    def respond(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append(request)
        return httpx.Response(status, content=body)

    return SriWebServices(Environment.TEST, transport=httpx.MockTransport(respond))


def test_a_voucher_goes_to_the_reception_service_inside_its_soap_envelope():
    asked: list[httpx.Request] = []
    document = signed(a_voucher())
    assert answering(RECEIVED, asked=asked).send(document).received is True
    request = asked[0]
    assert request.url.host == "celcer.sri.gob.ec" and request.url.path == RECEPTION_PATH
    assert request.headers["User-Agent"] == USER_AGENT
    carried = etree.fromstring(request.content).findtext(".//xml")
    assert base64.b64decode(carried) == document


def test_a_returned_voucher_carries_the_sris_messages():
    reception = answering(RETURNED).send(b"<factura/>")
    assert reception.received is False
    assert reception.messages[0].identifier == "43" and reception.messages[0].kind == "ERROR"
    assert reception.already_there is True


def test_a_voucher_turned_down_keeps_the_record_and_the_reasons():
    asked: list[httpx.Request] = []
    answer = answering(NOT_AUTHORIZED, asked=asked).authorize("1" * 49)
    assert asked[0].url.path == AUTHORIZATION_PATH
    assert answer is not None and answer.authorized is False
    assert answer.messages[0].text == "FIRMA INVALIDA"
    assert "<![CDATA[<factura/>]]>" in answer.record


def test_one_still_processing_is_asked_again_later():
    assert answering(PROCESSING).authorize("1" * 49) is None


@pytest.mark.parametrize(
    ("body", "status"), [(b"oops", 200), (b"<fault/>", 503), (RECEIVED[:40], 200)]
)
def test_an_sri_that_fails_or_mumbles_is_unavailable(body, status):
    with pytest.raises(SriUnavailable):
        answering(body, status).send(b"<factura/>")


def test_an_sri_that_cannot_be_reached_is_unavailable():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    sri = SriWebServices(Environment.TEST, transport=httpx.MockTransport(refuse))
    with pytest.raises(SriUnavailable):
        sri.authorize("1" * 49)


def test_the_simulated_sri_authorizes_what_it_received_and_nothing_else():
    sri = SimulatedSri()
    voucher = a_voucher()
    assert sri.authorize(voucher.access_key) is None
    assert sri.send(signed(voucher)).received is True
    answer = sri.authorize(voucher.access_key)
    assert answer is not None and answer.authorized is True
    record = etree.fromstring(answer.record.encode())
    assert record.findtext("numeroAutorizacion") == voucher.access_key
    assert record.findtext("ambiente") == "PRUEBAS"


def _lote_vouchers() -> tuple[Voucher, Voucher]:
    first = a_voucher()
    second = a_voucher(
        sequential=8,
        access_key=access_key(
            ISSUED_ON, VoucherKind.INVOICE, RUC, Environment.TEST, "001", "001", 8
        ),
    )
    return first, second


def test_a_lote_carries_its_vouchers_under_a_key_of_its_own():
    vouchers = _lote_vouchers()
    key = access_key(ISSUED_ON, VoucherKind.INVOICE, RUC, Environment.TEST, "001", "900", 1)
    lote = etree.fromstring(batch([signed(held) for held in vouchers], RUC, key))
    assert (lote.tag, lote.get("version"), lote.findtext("claveAcceso")) == ("lote", "1.0.0", key)
    sri = SimulatedSri()
    assert sri.send(etree.tostring(lote)).received is True
    answers = sri.authorize_batch(key)
    assert set(answers) == {held.access_key for held in vouchers}
    assert all(answer.authorized for answer in answers.values())


def test_a_lote_takes_from_one_to_fifty_vouchers():
    with pytest.raises(VoucherInvalid):
        batch([], RUC, "1")
    with pytest.raises(VoucherInvalid):
        batch([b"<factura/>"] * 51, RUC, "1")


def test_a_lotes_authorizations_come_back_by_each_vouchers_key():
    authorized, refused = _lote_vouchers()
    response = etree.fromstring(PROCESSING.replace(b"Comprobante>", b"Lote>"))
    held = response.find(".//autorizaciones")
    for voucher, state in ((authorized, "AUTORIZADO"), (refused, "NO AUTORIZADO")):
        record = etree.SubElement(held, "autorizacion")
        etree.SubElement(record, "estado").text = state
        etree.SubElement(record, "fechaAutorizacion").text = "2026-10-27T12:55:15-05:00"
        etree.SubElement(record, "comprobante").text = signed(voucher).decode()
        etree.SubElement(record, "mensajes")
    asked: list[httpx.Request] = []
    answers = answering(etree.tostring(response), asked=asked).authorize_batch("1")
    assert answers[authorized.access_key].authorized is True
    assert answers[refused.access_key].authorized is False
    assert b"autorizacionComprobanteLote" in asked[0].content
