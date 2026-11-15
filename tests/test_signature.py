from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.name import _ASN1Type
from cryptography.x509.oid import NameOID
from lxml import etree

from eqinvoice import (
    Credential,
    CredentialProblem,
    CredentialRefused,
    Payment,
    VoucherInvalid,
    details,
    issued_to,
    load_credential,
    price_line,
    read_credential,
    sign_voucher,
    simulated_credential,
    usable,
)
from eqinvoice.protocol import MOST_VOUCHER_BYTES
from eqinvoice.signature import C14N, DS, ETSI, RSA_SHA1, SIMULATED_SUBJECT, issuer_name
from tests.builders import CASH, FIXTURES, NOW, RUC, a_voucher, an_issuer, signed

PASSWORD = "clave-de-prueba"
TWO_KEYS_PASSWORD = "fixture-password"
ORGANIZATION_IDENTIFIER = x509.ObjectIdentifier("2.5.4.97")
BCE_RUC_FIELD = x509.ObjectIdentifier("1.3.6.1.4.1.37947.3.11")
OTHER_RUC = "1790099887001"
PRINTABLE_STRING = 0x13
SPECIAL_NAME = 'ACME, S.A. + "Hijos" <x>; a=b #1 \\ fin'


def _digest(element) -> str:
    return base64.b64encode(hashlib.sha1(etree.tostring(element, method="c14n")).digest()).decode()


def a_p12() -> bytes:
    held = simulated_credential()
    return pkcs12.serialize_key_and_certificates(
        b"firma", held.key, held.certificate, None, BestAvailableEncryption(PASSWORD.encode())
    )


def a_name(*relatives: Sequence[x509.NameAttribute]) -> x509.Name:
    return x509.Name([x509.RelativeDistinguishedName(relative) for relative in relatives])


def a_credential(
    issuer: x509.Name | None = None,
    subject: x509.Name | None = None,
    extensions: Sequence[x509.ExtensionType] = (),
) -> Credential:
    held = simulated_credential()
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject or held.certificate.subject)
        .issuer_name(issuer or held.certificate.issuer)
        .public_key(held.key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=1))
    )
    for extension in extensions:
        builder = builder.add_extension(extension, critical=False)
    return Credential(key=held.key, certificate=builder.sign(held.key, hashes.SHA256()))


def a_printable(text: str) -> bytes:
    return bytes([PRINTABLE_STRING, len(text)]) + text.encode()


def a_common_name(value: str, kind: _ASN1Type | None = None) -> x509.Name:
    attribute = (
        x509.NameAttribute(NameOID.COMMON_NAME, value, _type=kind)
        if kind
        else x509.NameAttribute(NameOID.COMMON_NAME, value)
    )
    return a_name([attribute])


def a_ruc_field(ruc: str) -> x509.UnrecognizedExtension:
    return x509.UnrecognizedExtension(BCE_RUC_FIELD, a_printable(ruc))


JAVA_NAMES = (
    (
        a_name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, SPECIAL_NAME)]),
        r"O=ACME\, S.A. \+ \"Hijos\" \<x\>\; a\=b \#1 \\ fin",
    ),
    (
        a_name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "  dos  ")],
            [x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "   ")],
            [x509.NameAttribute(NameOID.LOCALITY_NAME, "a b")],
        ),
        r"L=a b,OU=\ \ \ ,CN=\ \ dos\ \ ",
    ),
    (a_common_name("#uno"), r"CN=\#uno"),
    (a_common_name("a\x00b"), r"CN=a\00b"),
    (a_common_name("AUTORIDAD DE CERTIFICACIÓN ÑANDÚ"), "CN=AUTORIDAD DE CERTIFICACIÓN ÑANDÚ"),
    (a_common_name("ÁB", _ASN1Type.BMPString), "CN=ÁB"),
    (a_common_name("AB", _ASN1Type.UniversalString), "CN=#1c080000004100000042"),
    (
        a_name(
            [x509.NameAttribute(NameOID.COUNTRY_NAME, "EC")],
            [x509.NameAttribute(ORGANIZATION_IDENTIFIER, "VATEC-1790012345001")],
            [x509.NameAttribute(NameOID.COMMON_NAME, "EMPRESA")],
        ),
        "CN=EMPRESA,2.5.4.97=#0c1356415445432d31373930303132333435303031,C=EC",
    ),
    (
        a_name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "b"),
                x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "a"),
                x509.NameAttribute(ORGANIZATION_IDENTIFIER, f"TINEC-{RUC}"),
            ],
            [x509.NameAttribute(NameOID.COUNTRY_NAME, "EC")],
        ),
        "C=EC,CN=b+OU=a+2.5.4.97=#0c1354494e45432d31373930303131323233303031",
    ),
    (
        a_name(
            [x509.NameAttribute(NameOID.EMAIL_ADDRESS, "firma@example.ec")],
            [x509.NameAttribute(NameOID.SERIAL_NUMBER, "0102030405")],
            [x509.NameAttribute(NameOID.TITLE, "Gerente")],
        ),
        "2.5.4.12=#0c07476572656e7465,2.5.4.5=#130a30313032303330343035,"
        "1.2.840.113549.1.9.1=#16106669726d61406578616d706c652e6563",
    ),
    (
        a_name(
            [x509.NameAttribute(NameOID.DOMAIN_COMPONENT, "ec")],
            [x509.NameAttribute(NameOID.USER_ID, "jahir")],
            [x509.NameAttribute(NameOID.STREET_ADDRESS, "Av. 10 de Agosto")],
            [x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Loja")],
        ),
        "ST=Loja,STREET=Av. 10 de Agosto,UID=jahir,DC=ec",
    ),
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
    enciphering = a_credential(
        extensions=(x509.KeyUsage(False, False, True, False, False, False, False, False, False),)
    )
    data = pkcs12.serialize_key_and_certificates(
        b"cifrado",
        enciphering.key,
        enciphering.certificate,
        None,
        BestAvailableEncryption(PASSWORD.encode()),
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


@pytest.mark.parametrize(("issuer", "written"), JAVA_NAMES)
def test_the_issuer_is_written_as_java_writes_it(issuer, written):
    assert issuer_name(a_credential(issuer=issuer).certificate) == written


def test_the_signed_properties_name_the_issuer_as_java_does():
    tree = etree.fromstring(signed(a_voucher()))
    written = tree.findtext(".//ds:X509IssuerName", namespaces={"ds": DS})
    assert written == f"CN={SIMULATED_SUBJECT}"


def test_the_signature_names_its_rucs_by_organization_identifier_or_its_entitys_field():
    by_identifier = a_credential(
        subject=a_name([x509.NameAttribute(ORGANIZATION_IDENTIFIER, f"VATEC-{RUC}")])
    )
    assert details(by_identifier).rucs == (RUC,)
    assert details(a_credential(extensions=(a_ruc_field(OTHER_RUC),))).rucs == (OTHER_RUC,)
    assert details(simulated_credential()).rucs == ()


def test_a_signature_of_another_ruc_signs_nothing_and_one_naming_none_is_left_to_the_sri():
    with pytest.raises(CredentialRefused) as refused:
        sign_voucher(
            a_voucher(), an_issuer(), a_credential(extensions=(a_ruc_field(OTHER_RUC),)), NOW
        )
    assert refused.value.problem == CredentialProblem.NOT_THE_ISSUERS
    own = a_credential(extensions=(a_ruc_field(RUC),))
    assert issued_to(own, RUC) == own
    assert issued_to(simulated_credential(), RUC) == simulated_credential()


def test_a_voucher_heavier_than_the_sri_takes_is_refused():
    lines = tuple(
        price_line(f"SKU-{index}", "x" * 300, Decimal("1"), Decimal("1"), "0")
        for index in range(700)
    )
    heavy = a_voucher(lines=lines, payments=(Payment(CASH, Decimal("700.00")),))
    with pytest.raises(VoucherInvalid, match=str(MOST_VOUCHER_BYTES)):
        sign_voucher(heavy, an_issuer(), simulated_credential(), NOW)
