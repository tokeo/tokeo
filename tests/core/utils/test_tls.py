"""
Tests for create_certificate of tokeo.core.utils.tls.

The key types and sizes, the subject alt names typed as ip or dns name, the
random serial number, a CA and a certificate signed by it, and the refused
arguments.
"""

import ipaddress
from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from tokeo.core.utils.tls import create_certificate


def _get_extension(certificate, extension_class):
    # the value of an extension of the certificate
    return certificate.extensions.get_extension_for_class(extension_class).value


def test_create_certificate_defaults():
    """By default an ec P-384 server certificate, self-signed, valid for 90 days."""
    certificate, key, cert_pem, key_pem = create_certificate('localhost')
    assert isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name == 'secp384r1'
    assert certificate.subject == certificate.issuer
    assert certificate.not_valid_after_utc - certificate.not_valid_before_utc == timedelta(days=90)
    assert _get_extension(certificate, x509.BasicConstraints).ca is False
    assert list(_get_extension(certificate, x509.ExtendedKeyUsage)) == [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
    assert cert_pem.startswith(b'-----BEGIN CERTIFICATE-----')
    assert key_pem.startswith(b'-----BEGIN PRIVATE KEY-----')


def test_create_certificate_rsa_key():
    """An rsa key of 2048 bits also encrypts the key exchange."""
    certificate, key, _, _ = create_certificate('localhost', key_type='rsa', key_size=2048)
    assert isinstance(key, rsa.RSAPrivateKey) and key.key_size == 2048
    assert _get_extension(certificate, x509.KeyUsage).key_encipherment is True


def test_create_certificate_types_the_sans_and_drops_duplicates():
    """An ip address becomes an ip entry, any other name a dns entry, each once."""
    certificate, _, _, _ = create_certificate('localhost', sans=['localhost', '127.0.0.1', 'localhost', '::1'])
    assert list(_get_extension(certificate, x509.SubjectAlternativeName)) == [
        x509.DNSName('localhost'),
        x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
        x509.IPAddress(ipaddress.ip_address('::1')),
    ]


def test_create_certificate_without_sans_has_no_san_extension():
    """Without sans the certificate carries no subject alt name extension."""
    certificate, _, _, _ = create_certificate('Tokeo Test CA', is_ca=True)
    with pytest.raises(x509.ExtensionNotFound):
        _get_extension(certificate, x509.SubjectAlternativeName)


def test_create_certificate_gives_every_certificate_a_random_serial_number():
    """Two certificates of the same name get two serial numbers, never 1."""
    first, _, _, _ = create_certificate('localhost')
    second, _, _, _ = create_certificate('localhost')
    assert first.serial_number != second.serial_number
    assert 1 not in (first.serial_number, second.serial_number)


def test_create_certificate_signed_by_a_ca():
    """A CA signs a certificate, its key identifiers link the two."""
    ca_certificate, ca_key, _, _ = create_certificate('Tokeo Test CA', is_ca=True)
    certificate, _, _, _ = create_certificate('localhost', sans=['localhost'], issuer=(ca_certificate, ca_key))
    assert _get_extension(ca_certificate, x509.BasicConstraints).ca is True
    assert _get_extension(ca_certificate, x509.KeyUsage).key_cert_sign is True
    assert certificate.issuer == ca_certificate.subject
    certificate.verify_directly_issued_by(ca_certificate)
    authority_key_id = _get_extension(certificate, x509.AuthorityKeyIdentifier).key_identifier
    assert authority_key_id == _get_extension(ca_certificate, x509.SubjectKeyIdentifier).digest


@pytest.mark.parametrize(
    'kwargs, expected',
    [
        (dict(key_type='dsa'), "key_type must be 'ec' or 'rsa', not 'dsa'"),
        (dict(key_size=192), 'key_size 192 is not valid for ec, use 256, 384 or 521'),
        (dict(key_type='rsa', key_size=1024), 'key_size 1024 is not valid for rsa, use 2048 or greater'),
        (dict(key_type='rsa', key_size='2048'), "key_size '2048' is not valid for rsa, use 2048 or greater"),
        (dict(valid_days=0), 'valid_days must be an integer greater than 0, not 0'),
        (dict(valid_days=True), 'valid_days must be an integer greater than 0, not True'),
    ],
    ids=['key_type', 'ec_size', 'rsa_size', 'rsa_size_str', 'days_zero', 'days_bool'],
)
def test_create_certificate_refuses_a_wrong_argument(kwargs, expected):
    """A wrong key_type, key_size or valid_days raises ValueError naming it."""
    with pytest.raises(ValueError) as err_info:
        create_certificate('localhost', **kwargs)
    assert str(err_info.value) == expected
