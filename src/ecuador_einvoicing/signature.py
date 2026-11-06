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

from ecuador_einvoicing.document import build, validate
from ecuador_einvoicing.errors import CredentialProblem, CredentialRefused
from ecuador_einvoicing.model import Issuer, Voucher
from ecuador_einvoicing.protocol import CLOCK, VOUCHER_ID

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


def _matching(key: object, certificates: Sequence[x509.Certificate]) -> x509.Certificate | None:
    if not isinstance(key, rsa.RSAPrivateKey):
        return None
    wanted = key.public_key().public_numbers()
    for certificate in certificates:
        public = certificate.public_key()
        if isinstance(public, rsa.RSAPublicKey) and public.public_numbers() == wanted:
            return certificate
    return None


def load_credential(data: bytes, password: str) -> Credential:
    try:
        bundle = pkcs12.load_pkcs12(data, password.encode())
    except ValueError as unreadable:
        raise CredentialRefused(
            CredentialProblem.UNREADABLE, f"the .p12 signature could not be opened: {unreadable}"
        ) from None
    candidates = [bundle.cert.certificate] if bundle.cert else []
    candidates += [extra.certificate for extra in bundle.additional_certs]
    certificate = _matching(bundle.key, candidates)
    if certificate is None or not isinstance(bundle.key, rsa.RSAPrivateKey):
        raise CredentialRefused(
            CredentialProblem.UNREADABLE, "the .p12 holds no RSA key with its own certificate"
        )
    return Credential(key=bundle.key, certificate=certificate)


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
    etree.SubElement(
        issuer_serial, _ds("X509IssuerName")
    ).text = certificate.issuer.rfc4514_string()
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


def sign_voucher(voucher: Voucher, issuer: Issuer, credential: Credential, now: datetime) -> bytes:
    """The voucher built, signed and checked against the SRI's schema, ready to send."""
    document = sign(build(voucher, issuer), usable(credential, now), now)
    validate(voucher.kind, document)
    return document
