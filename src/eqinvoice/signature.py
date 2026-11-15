from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from eqinvoice.document import build, validate
from eqinvoice.errors import CredentialProblem, CredentialRefused, VoucherInvalid
from eqinvoice.model import AnyVoucher, Issuer
from eqinvoice.protocol import CLOCK, MOST_VOUCHER_BYTES, VOUCHER_ID

DS = "http://www.w3.org/2000/09/xmldsig#"
ETSI = "http://uri.etsi.org/01903/v1.3.2#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
SHA1 = "http://www.w3.org/2000/09/xmldsig#sha1"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
SIGNED_PROPERTIES = "http://uri.etsi.org/01903#SignedProperties"
OBJECT_DESCRIPTION = "contenido comprobante"
OBJECT_MIME = "text/xml"
SIMULATED_SUBJECT = "Simulated SRI signature"
SIMULATED_YEARS = 10
KEY_BITS = 2048
PUBLIC_EXPONENT = 65537
ID_DIGITS = 6
LONG_FORM = 0x80
OID_ARC = 40
PKCS7_DATA = bytes.fromhex("2a864886f70d010701")
SHROUDED_KEY_BAG = bytes.fromhex("2a864886f70d010c0a0102")
RFC2253_KEYWORDS = {
    "2.5.4.3": "CN",
    "2.5.4.6": "C",
    "2.5.4.7": "L",
    "2.5.4.8": "ST",
    "2.5.4.9": "STREET",
    "2.5.4.10": "O",
    "2.5.4.11": "OU",
    "0.9.2342.19200300.100.1.1": "UID",
    "0.9.2342.19200300.100.1.25": "DC",
}
RFC2253_ESCAPED = ',=+<>#;"\\'
RFC2253_EDGES = " \r"
RFC2253_NUL = "\\00"
DIRECTORY_STRINGS = {
    0x0C: "utf-8",
    0x13: "latin-1",
    0x14: "latin-1",
    0x16: "latin-1",
    0x1B: "latin-1",
    0x1E: "utf-16-be",
}
ORGANIZATION_IDENTIFIER = x509.ObjectIdentifier("2.5.4.97")
TAXPAYER_PREFIX, COMPANY_PREFIX = "TINEC-", "VATEC-"
RUC_DIGITS = 13
CERTIFICATE_RUC_FIELDS = frozenset(
    {
        "1.3.6.1.4.1.37746.3.11",
        "1.3.6.1.4.1.37947.3.11",
        "1.3.6.1.4.1.18332.3.11",
        "1.3.6.1.4.1.37442.3.11",
        "1.3.6.1.4.1.43745.1.3.11",
        "1.3.6.1.4.1.55519.1.1.1.5.2.4",
        "1.3.6.1.4.1.47286.102.3.11",
        "1.3.6.1.4.1.52643.3.11",
        "1.3.6.1.4.1.57153.102.3.11",
        "1.3.6.1.4.1.59198.3.11",
        "1.3.6.1.4.1.59382.3.11",
        "1.3.6.1.4.1.34380.3.11",
        "1.3.6.1.4.1.61305.3.11",
        "1.3.6.1.4.1.56105.3.11",
    }
)


@dataclass(frozen=True)
class Credential:
    key: rsa.RSAPrivateKey
    certificate: x509.Certificate


@dataclass(frozen=True)
class SignatureDetails:
    subject: str
    issuer: str
    serial: str
    valid_from: datetime
    valid_until: datetime
    rucs: tuple[str, ...] = ()


def _matching(key: object, certificates: Sequence[x509.Certificate]) -> x509.Certificate | None:
    if not isinstance(key, rsa.RSAPrivateKey):
        return None
    wanted = key.public_key().public_numbers()
    for certificate in certificates:
        public = certificate.public_key()
        if isinstance(public, rsa.RSAPublicKey) and public.public_numbers() == wanted:
            return certificate
    return None


def _element(data: bytes) -> tuple[bytes, bytes, bytes]:
    """The first DER element's content, the element whole, and whatever follows it."""
    length, start = data[1], 2
    if length & LONG_FORM:
        count = length ^ LONG_FORM
        if not count:
            raise ValueError("indefinite lengths are BER, not DER")
        length, start = int.from_bytes(data[start : start + count], "big"), start + count
    end = start + length
    return data[start:end], data[:end], data[end:]


def _content(data: bytes) -> tuple[bytes, bytes]:
    content, _, rest = _element(data)
    return content, rest


def _elements(data: bytes, whole: bool = False) -> list[bytes]:
    found = []
    while data:
        content, element, data = _element(data)
        found.append(element if whole else content)
    return found


def _dotted(content: bytes) -> str:
    numbers, value = [], 0
    for byte in content:
        value = value << 7 | byte & ~LONG_FORM
        if not byte & LONG_FORM:
            numbers.append(value)
            value = 0
    arc = min(numbers[0] // OID_ARC, 2)
    return ".".join(str(number) for number in (arc, numbers[0] - arc * OID_ARC, *numbers[1:]))


def _text(element: bytes) -> str | None:
    """A DER directory string's text, decoded as Java does; None for any other value."""
    codec = DIRECTORY_STRINGS.get(element[0])
    return _content(element)[0].decode(codec, errors="replace") if codec else None


def _escaped(value: str) -> str:
    inner = "".join(
        RFC2253_NUL if char == "\0" else f"\\{char}" if char in RFC2253_ESCAPED else char
        for char in value
    )
    lead, trail = len(inner) - len(inner.lstrip(RFC2253_EDGES)), len(inner.rstrip(RFC2253_EDGES))
    return "".join(
        char if lead <= index < trail else f"\\{char}" for index, char in enumerate(inner)
    )


def issuer_name(certificate: x509.Certificate) -> str:
    """The issuer as Java writes it in RFC 2253, as the SRI reads it: keywords, else #DER hex."""
    written = []
    for relative in reversed(_elements(_content(certificate.issuer.public_bytes())[0])):
        pairs = []
        for attribute in _elements(relative):
            kind, value = _elements(attribute, whole=True)
            oid = _dotted(_content(kind)[0])
            keyword = RFC2253_KEYWORDS.get(oid)
            text = _text(value) if keyword else None
            written_value = _escaped(text) if text is not None else f"#{value.hex()}"
            pairs.append(f"{keyword or oid}={written_value}")
        written.append("+".join(pairs))
    return ",".join(written)


def certificate_rucs(certificate: x509.Certificate) -> tuple[str, ...]:
    """The RUCs a certificate names: in its organizationIdentifier or its entity's own field."""
    named = [
        str(attribute.value).removeprefix(TAXPAYER_PREFIX).removeprefix(COMPANY_PREFIX)
        for attribute in certificate.subject.get_attributes_for_oid(ORGANIZATION_IDENTIFIER)
    ]
    for extension in certificate.extensions:
        if extension.oid.dotted_string in CERTIFICATE_RUC_FIELDS:
            try:
                named.append(_text(getattr(extension.value, "value", b"")) or "")
            except (IndexError, ValueError):
                continue
    return tuple(dict.fromkeys(ruc for ruc in named if len(ruc) == RUC_DIGITS and ruc.isdigit()))


def _wrapped(explicit: bytes) -> list[bytes]:
    return _elements(_content(_content(explicit)[0])[0])


def _shrouded_keys(data: bytes) -> list[bytes]:
    """Every shrouded key in the plain safe contents; a BCE .p12 also has one to encipher."""
    _, safe, *_ = _elements(_content(data)[0])
    found = []
    for info in _wrapped(_elements(safe)[1]):
        kind, inner = _elements(info)
        if kind == PKCS7_DATA:
            found += [
                value
                for bag_id, value, *_ in (_elements(bag) for bag in _wrapped(inner))
                if bag_id == SHROUDED_KEY_BAG
            ]
    return found


def _keys(data: bytes, password: bytes, first: object) -> list[object]:
    try:
        held: list[object] = [
            serialization.load_der_private_key(key, password) for key in _shrouded_keys(data)
        ]
    except (IndexError, ValueError):
        return [first]
    return held or [first]


def _signs(certificate: x509.Certificate) -> bool:
    try:
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return True
    return usage.digital_signature


def load_credential(data: bytes, password: str) -> Credential:
    """The .p12's key whose certificate signs, past any key that only enciphers."""
    try:
        bundle = pkcs12.load_pkcs12(data, password.encode())
    except ValueError as unreadable:
        raise CredentialRefused(
            CredentialProblem.UNREADABLE, f"the .p12 signature could not be opened: {unreadable}"
        ) from None
    candidates = [bundle.cert.certificate] if bundle.cert else []
    candidates += [extra.certificate for extra in bundle.additional_certs]
    pairs = [
        (key, certificate)
        for key in _keys(data, password.encode(), bundle.key)
        if isinstance(key, rsa.RSAPrivateKey) and (certificate := _matching(key, candidates))
    ]
    if not pairs:
        raise CredentialRefused(
            CredentialProblem.UNREADABLE, "the .p12 holds no RSA key with its own certificate"
        )
    signing = next(((key, held) for key, held in pairs if _signs(held)), None)
    if signing is None:
        raise CredentialRefused(
            CredentialProblem.NOT_FOR_SIGNING,
            "the .p12's certificates are not for digital signature (key usage digitalSignature)",
        )
    key, certificate = signing
    return Credential(key=key, certificate=certificate)


def read_credential(path: Path, password: str) -> Credential:
    try:
        data = path.read_bytes()
    except OSError as missing:
        raise CredentialRefused(
            CredentialProblem.MISSING, f"the .p12 signature could not be read: {missing}"
        ) from None
    return load_credential(data, password)


@cache
def simulated_credential(subject: str = SIMULATED_SUBJECT) -> Credential:
    """A self-signed signature for development and tests; the SRI itself would refuse it."""
    key = rsa.generate_private_key(public_exponent=PUBLIC_EXPONENT, key_size=KEY_BITS)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365 * SIMULATED_YEARS))
        .sign(key, hashes.SHA256())
    )
    return Credential(key=key, certificate=certificate)


def usable(credential: Credential, now: datetime) -> Credential:
    held = credential.certificate
    if not held.not_valid_before_utc <= now <= held.not_valid_after_utc:
        raise CredentialRefused(
            CredentialProblem.OUT_OF_DATE,
            f"the signature is valid from {held.not_valid_before_utc:%Y-%m-%d}"
            f" to {held.not_valid_after_utc:%Y-%m-%d}",
        )
    return credential


def issued_to(credential: Credential, ruc: str) -> Credential:
    """Refused if it names RUCs and not the issuer's; naming none, the SRI judges it."""
    named = certificate_rucs(credential.certificate)
    if named and ruc not in named:
        raise CredentialRefused(
            CredentialProblem.NOT_THE_ISSUERS,
            f"the signature belongs to {', '.join(named)}, not to the issuer {ruc}",
        )
    return credential


def _named(name: x509.Name) -> str:
    common = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(common[0].value) if common else name.rfc4514_string()


def details(credential: Credential) -> SignatureDetails:
    """The signature as a person reads it, whether or not it is still valid."""
    held = credential.certificate
    return SignatureDetails(
        subject=_named(held.subject),
        issuer=_named(held.issuer),
        serial=str(held.serial_number),
        valid_from=held.not_valid_before_utc,
        valid_until=held.not_valid_after_utc,
        rucs=certificate_rucs(held),
    )


def _digest(data: bytes) -> str:
    return base64.b64encode(hashlib.sha1(data, usedforsecurity=False).digest()).decode()


def _c14n(element: etree._Element) -> bytes:
    return etree.tostring(element, method="c14n")


def _b64_integer(value: int) -> str:
    return base64.b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).decode()


def _ds(tag: str) -> str:
    return f"{{{DS}}}{tag}"


def _etsi(tag: str) -> str:
    return f"{{{ETSI}}}{tag}"


def _digest_method(parent: etree._Element) -> None:
    etree.SubElement(parent, _ds("DigestMethod"), Algorithm=SHA1)


def _reference(
    signed_info: etree._Element, uri: str, digest: str, **attributes: str
) -> etree._Element:
    reference = etree.SubElement(signed_info, _ds("Reference"), **attributes, URI=uri)
    _digest_method(reference)
    etree.SubElement(reference, _ds("DigestValue")).text = digest
    return reference


def _ids() -> dict[str, str]:
    def fresh() -> int:
        return secrets.randbelow(10**ID_DIGITS)

    signature = f"Signature{fresh()}"
    return {
        "signature": signature,
        "signed_info": f"Signature-SignedInfo{fresh()}",
        "signed_properties": f"{signature}-SignedProperties{fresh()}",
        "signed_properties_reference": f"SignedPropertiesID{fresh()}",
        "certificate": f"Certificate{fresh()}",
        "reference": f"Reference-ID-{fresh()}",
        "signature_value": f"SignatureValue{fresh()}",
        "object": f"{signature}-Object{fresh()}",
    }


def _signed_properties(
    parent: etree._Element, ids: dict[str, str], credential: Credential, at: datetime
) -> etree._Element:
    certificate = credential.certificate
    der = certificate.public_bytes(serialization.Encoding.DER)
    qualifying = etree.SubElement(
        parent, _etsi("QualifyingProperties"), Target=f"#{ids['signature']}"
    )
    properties = etree.SubElement(
        qualifying, _etsi("SignedProperties"), Id=ids["signed_properties"]
    )
    signature_properties = etree.SubElement(properties, _etsi("SignedSignatureProperties"))
    etree.SubElement(signature_properties, _etsi("SigningTime")).text = at.astimezone(
        CLOCK
    ).isoformat(timespec="seconds")
    cert = etree.SubElement(
        etree.SubElement(signature_properties, _etsi("SigningCertificate")), _etsi("Cert")
    )
    digest = etree.SubElement(cert, _etsi("CertDigest"))
    _digest_method(digest)
    etree.SubElement(digest, _ds("DigestValue")).text = _digest(der)
    issuer_serial = etree.SubElement(cert, _etsi("IssuerSerial"))
    etree.SubElement(issuer_serial, _ds("X509IssuerName")).text = issuer_name(certificate)
    etree.SubElement(issuer_serial, _ds("X509SerialNumber")).text = str(certificate.serial_number)
    data_object = etree.SubElement(
        etree.SubElement(properties, _etsi("SignedDataObjectProperties")),
        _etsi("DataObjectFormat"),
        ObjectReference=f"#{ids['reference']}",
    )
    etree.SubElement(data_object, _etsi("Description")).text = OBJECT_DESCRIPTION
    etree.SubElement(data_object, _etsi("MimeType")).text = OBJECT_MIME
    return properties


def _key_info(
    parent: etree._Element, ids: dict[str, str], credential: Credential
) -> etree._Element:
    der = credential.certificate.public_bytes(serialization.Encoding.DER)
    numbers = credential.key.public_key().public_numbers()
    key_info = etree.SubElement(parent, _ds("KeyInfo"), Id=ids["certificate"])
    x509_data = etree.SubElement(key_info, _ds("X509Data"))
    etree.SubElement(x509_data, _ds("X509Certificate")).text = base64.b64encode(der).decode()
    rsa_value = etree.SubElement(etree.SubElement(key_info, _ds("KeyValue")), _ds("RSAKeyValue"))
    etree.SubElement(rsa_value, _ds("Modulus")).text = _b64_integer(numbers.n)
    etree.SubElement(rsa_value, _ds("Exponent")).text = _b64_integer(numbers.e)
    return key_info


def sign(voucher: etree._Element, credential: Credential, at: datetime) -> bytes:
    voucher_digest = _digest(_c14n(voucher))
    ids = _ids()
    signature = etree.SubElement(
        voucher, _ds("Signature"), nsmap={"ds": DS, "etsi": ETSI}, Id=ids["signature"]
    )
    signed_info = etree.SubElement(signature, _ds("SignedInfo"), Id=ids["signed_info"])
    etree.SubElement(signed_info, _ds("CanonicalizationMethod"), Algorithm=C14N)
    etree.SubElement(signed_info, _ds("SignatureMethod"), Algorithm=RSA_SHA1)
    signature_value = etree.SubElement(signature, _ds("SignatureValue"), Id=ids["signature_value"])
    key_info = _key_info(signature, ids, credential)
    holder = etree.SubElement(signature, _ds("Object"), Id=ids["object"])
    properties = _signed_properties(holder, ids, credential, at)
    _reference(
        signed_info,
        f"#{ids['signed_properties']}",
        _digest(_c14n(properties)),
        Id=ids["signed_properties_reference"],
        Type=SIGNED_PROPERTIES,
    )
    _reference(signed_info, f"#{ids['certificate']}", _digest(_c14n(key_info)))
    voucher_reference = etree.Element(_ds("Reference"), Id=ids["reference"], URI=f"#{VOUCHER_ID}")
    signed_info.append(voucher_reference)
    transforms = etree.SubElement(voucher_reference, _ds("Transforms"))
    etree.SubElement(transforms, _ds("Transform"), Algorithm=ENVELOPED)
    _digest_method(voucher_reference)
    etree.SubElement(voucher_reference, _ds("DigestValue")).text = voucher_digest
    signed = credential.key.sign(_c14n(signed_info), padding.PKCS1v15(), hashes.SHA1())
    signature_value.text = base64.b64encode(signed).decode()
    return etree.tostring(voucher, xml_declaration=True, encoding="UTF-8")


def sign_voucher(
    voucher: AnyVoucher, issuer: Issuer, credential: Credential, now: datetime
) -> bytes:
    """The voucher built, signed and checked against the SRI's schema, ready to send."""
    document = sign(build(voucher, issuer), issued_to(usable(credential, now), issuer.ruc), now)
    validate(voucher.kind, document)
    if len(document) > MOST_VOUCHER_BYTES:
        raise VoucherInvalid(
            f"A voucher weighs at most {MOST_VOUCHER_BYTES} bytes, not {len(document)}"
        )
    return document
