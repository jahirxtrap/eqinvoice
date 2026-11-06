from __future__ import annotations

import base64
import ssl
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
from lxml import etree

from ecuador_einvoicing.errors import SriUnavailable
from ecuador_einvoicing.model import Environment
from ecuador_einvoicing.protocol import (
    ALREADY_REGISTERED,
    AUTHORIZATION_NS,
    AUTHORIZATION_PATH,
    AUTHORIZED,
    CLOCK,
    ENVIRONMENT_CODES,
    ENVIRONMENT_NAMES,
    HOSTS,
    NOT_AUTHORIZED,
    PARSER,
    RECEIVED,
    RECEPTION_NS,
    RECEPTION_PATH,
    RETURNED,
    SOAP_NS,
    STILL_PROCESSING,
)
from ecuador_einvoicing.protocol import text_at as _text

TIMEOUT_SECONDS = 30.0
SERVER_FAILURE = 500
SOAP_HEADERS = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'}


@dataclass(frozen=True)
class Message:
    identifier: str
    text: str
    detail: str
    kind: str


@dataclass(frozen=True)
class Reception:
    received: bool
    messages: tuple[Message, ...]

    @property
    def already_there(self) -> bool:
        """Sent before, so its answer waits at the authorization service."""
        return any(
            message.identifier in (ALREADY_REGISTERED, STILL_PROCESSING)
            for message in self.messages
        )


@dataclass(frozen=True)
class Authorization:
    authorized: bool
    authorized_at: datetime | None
    record: str
    messages: tuple[Message, ...]


class Sri(Protocol):
    def send(self, document: bytes) -> Reception: ...

    def authorize(self, access_key: str) -> Authorization | None: ...


def _messages(element: etree._Element | None) -> tuple[Message, ...]:
    if element is None:
        return ()
    return tuple(
        Message(
            identifier=_text(message, "identificador"),
            text=_text(message, "mensaje"),
            detail=_text(message, "informacionAdicional"),
            kind=_text(message, "tipo"),
        )
        for message in element.iterfind(".//mensaje")
    )


def _envelope(namespace: str, operation: str, argument: str, value: str) -> bytes:
    envelope = etree.Element(f"{{{SOAP_NS}}}Envelope", nsmap={"soapenv": SOAP_NS, "ec": namespace})
    etree.SubElement(envelope, f"{{{SOAP_NS}}}Header")
    call = etree.SubElement(
        etree.SubElement(envelope, f"{{{SOAP_NS}}}Body"), f"{{{namespace}}}{operation}"
    )
    etree.SubElement(call, argument).text = value
    return etree.tostring(envelope, xml_declaration=True, encoding="UTF-8")


def _serialized(record: etree._Element) -> str:
    return etree.tostring(record, xml_declaration=True, encoding="UTF-8").decode()


def _stamp(written: str) -> datetime | None:
    try:
        return datetime.fromisoformat(written)
    except ValueError:
        return None


def _kept(answered: etree._Element) -> etree._Element:
    """The SRI's record as it answered it, with the voucher inside kept as character data."""
    record = etree.Element("autorizacion")
    for child in answered:
        if child.tag == "comprobante":
            etree.SubElement(record, "comprobante").text = etree.CDATA(child.text or "")
        else:
            record.append(deepcopy(child))
    return record


class SriWebServices:
    def __init__(
        self,
        environment: Environment,
        timeout: float = TIMEOUT_SECONDS,
        verify: ssl.SSLContext | bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.environment = environment
        self._client = httpx.Client(
            base_url=HOSTS[environment], timeout=timeout, verify=verify, transport=transport
        )

    def _post(self, path: str, body: bytes) -> etree._Element:
        try:
            response = self._client.post(path, content=body, headers=SOAP_HEADERS)
        except httpx.HTTPError as unreachable:
            raise SriUnavailable(str(unreachable)) from unreachable
        if response.status_code >= SERVER_FAILURE:
            raise SriUnavailable(f"{response.status_code} from the SRI")
        try:
            return etree.fromstring(response.content, PARSER)
        except etree.XMLSyntaxError as unreadable:
            raise SriUnavailable(
                f"the SRI answered something that is not XML: {unreadable}"
            ) from None

    def send(self, document: bytes) -> Reception:
        payload = base64.b64encode(document).decode()
        answer = self._post(
            RECEPTION_PATH, _envelope(RECEPTION_NS, "validarComprobante", "xml", payload)
        )
        state = _text(answer, ".//RespuestaRecepcionComprobante/estado")
        if state not in (RECEIVED, RETURNED):
            raise SriUnavailable(f"the reception service answered without a state: {state!r}")
        return Reception(
            received=state == RECEIVED,
            messages=_messages(answer.find(".//RespuestaRecepcionComprobante/comprobantes")),
        )

    def authorize(self, access_key: str) -> Authorization | None:
        """How the voucher stands at the authorization service; None while still processing."""
        answer = self._post(
            AUTHORIZATION_PATH,
            _envelope(
                AUTHORIZATION_NS, "autorizacionComprobante", "claveAccesoComprobante", access_key
            ),
        )
        records = answer.findall(".//RespuestaAutorizacionComprobante/autorizaciones/autorizacion")
        if not records:
            return None
        chosen = next(
            (record for record in records if _text(record, "estado") == AUTHORIZED), records[-1]
        )
        state = _text(chosen, "estado")
        if state not in (AUTHORIZED, NOT_AUTHORIZED):
            return None
        return Authorization(
            authorized=state == AUTHORIZED,
            authorized_at=_stamp(_text(chosen, "fechaAutorizacion")),
            record=_serialized(_kept(chosen)),
            messages=_messages(chosen.find("mensajes")),
        )


def _now() -> datetime:
    return datetime.now(UTC)


class SimulatedSri:
    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock
        self._received: dict[str, bytes] = {}

    def send(self, document: bytes) -> Reception:
        voucher = etree.fromstring(document, PARSER)
        self._received[_text(voucher, "infoTributaria/claveAcceso")] = document
        return Reception(received=True, messages=())

    def authorize(self, access_key: str) -> Authorization | None:
        document = self._received.get(access_key)
        if document is None:
            return None
        voucher = etree.fromstring(document, PARSER)
        codes = {code: environment for environment, code in ENVIRONMENT_CODES.items()}
        environment = codes[_text(voucher, "infoTributaria/ambiente")]
        now = self._clock()
        record = etree.Element("autorizacion")
        etree.SubElement(record, "estado").text = AUTHORIZED
        etree.SubElement(record, "numeroAutorizacion").text = access_key
        etree.SubElement(record, "fechaAutorizacion").text = now.astimezone(CLOCK).isoformat(
            timespec="seconds"
        )
        etree.SubElement(record, "ambiente").text = ENVIRONMENT_NAMES[environment]
        etree.SubElement(record, "comprobante").text = etree.CDATA(document.decode())
        etree.SubElement(record, "mensajes")
        return Authorization(
            authorized=True, authorized_at=now, record=_serialized(record), messages=()
        )
