from __future__ import annotations

import base64
import hashlib
from copy import deepcopy
from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from eqinvoice import (
    CredentialProblem,
    CredentialRefused,
    details,
    load_credential,
    read_credential,
    simulated_credential,
    usable,
)
from eqinvoice.signature import C14N, DS, ETSI, RSA_SHA1, SIMULATED_SUBJECT
from tests.builders import FIXTURES, NOW, a_voucher, signed

PASSWORD = "clave-de-prueba"
TWO_KEYS_PASSWORD = "fixture-password"


def _digest(element) -> str:
    return base64.b64encode(hashlib.sha1(etree.tostring(element, method="c14n")).digest()).decode()


def a_p12() -> bytes:
    held = simulated_credential()
    return pkcs12.serialize_key_and_certificates(
        b"firma", held.key, held.certificate, None, BestAvailableEncryption(PASSWORD.encode())
    )


def test_every_reference_digests_what_it_names():
    tree = etree.fromstring(signed(a_voucher()))
    ns = {"ds": DS, "etsi": ETSI}
    signature = tree.find("ds:Signature", ns)
    bare = deepcopy(tree)
    bare.remove(bare.find("ds:Signature", ns))
    by_uri = {
        reference.get("URI"): reference.findtext("ds:DigestValue", namespaces=ns)
        for reference in signature.iterfind("ds:SignedInfo/ds:Reference", ns)
    }
    assert by_uri["#comprobante"] == _digest(bare)
    key_info = signature.find("ds:KeyInfo", ns)
    assert by_uri[f"#{key_info.get('Id')}"] == _digest(key_info)
    properties = signature.find(".//etsi:SignedProperties", ns)
    assert by_uri[f"#{properties.get('Id')}"] == _digest(properties)


def test_the_signature_value_is_the_certificates_key_over_the_signed_info():
    tree = etree.fromstring(signed(a_voucher()))
    ns = {"ds": DS}
    signature = tree.find("ds:Signature", ns)
    value = base64.b64decode(signature.findtext("ds:SignatureValue", namespaces=ns))
    public = simulated_credential().certificate.public_key()
    assert isinstance(public, rsa.RSAPublicKey)
    public.verify(
        value,
        etree.tostring(signature.find("ds:SignedInfo", ns), method="c14n"),
        padding.PKCS1v15(),
        hashes.SHA1(),
    )


def test_it_uses_the_algorithms_the_sri_asks_for():
    info = etree.fromstring(signed(a_voucher())).find("ds:Signature/ds:SignedInfo", {"ds": DS})
    ns = {"ds": DS}
    assert info.find("ds:SignatureMethod", ns).get("Algorithm") == RSA_SHA1
    assert info.find("ds:CanonicalizationMethod", ns).get("Algorithm") == C14N
    assert len(info.findall("ds:Reference", ns)) == 3


def test_a_p12_opens_with_its_password_and_holds_its_own_certificate():
    opened = load_credential(a_p12(), PASSWORD)
    assert opened.certificate == simulated_credential().certificate


def test_a_p12_with_a_key_to_encipher_first_signs_with_the_key_that_signs():
    opened = read_credential(FIXTURES / "two-keys.p12", TWO_KEYS_PASSWORD)
    usage = opened.certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.digital_signature and usage.content_commitment and not usage.key_encipherment
    assert opened.certificate.public_key() == opened.key.public_key()
    usable(opened, NOW)


def test_a_p12_that_only_enciphers_says_so():
    held = simulated_credential()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Solo cifrado")])
    enciphering = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(held.key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=1))
        .add_extension(
            x509.KeyUsage(False, False, True, False, False, False, False, False, False),
            critical=True,
        )
        .sign(held.key, hashes.SHA256())
    )
    data = pkcs12.serialize_key_and_certificates(
        b"cifrado", held.key, enciphering, None, BestAvailableEncryption(PASSWORD.encode())
    )
    with pytest.raises(CredentialRefused) as refused:
        load_credential(data, PASSWORD)
    assert refused.value.problem == CredentialProblem.NOT_FOR_SIGNING


def test_a_wrong_password_or_a_missing_file_say_which_it_was(tmp_path):
    with pytest.raises(CredentialRefused) as wrong:
        load_credential(a_p12(), "otra")
    assert wrong.value.problem == CredentialProblem.UNREADABLE
    with pytest.raises(CredentialRefused) as missing:
        read_credential(tmp_path / "firma.p12", PASSWORD)
    assert missing.value.problem == CredentialProblem.MISSING


def test_a_signature_out_of_date_signs_nothing():
    later = NOW + timedelta(days=365 * 20)
    with pytest.raises(CredentialRefused) as expired:
        usable(simulated_credential(), later)
    assert expired.value.problem == CredentialProblem.OUT_OF_DATE


def test_it_reads_whom_the_signature_names_by_common_name():
    held = details(simulated_credential())
    assert (held.subject, held.issuer) == (SIMULATED_SUBJECT, SIMULATED_SUBJECT)
    assert held.valid_from < NOW < held.valid_until
