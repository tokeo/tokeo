"""
Tests for tokeo.core.utils.memfd.

Cover the RAM-backed path primitive: a single blob yields a readable path, many
blobs yield independent paths, str is encoded, and the real consumer works
(ssl.SSLContext.load_cert_chain from PEM in memory). The platform branches are
exercised too: forcing the /dev/fd pipe path (as on macOS) and raising when the
platform offers neither backing (as on Windows).
"""

import os
import ssl
from datetime import datetime, timedelta, timezone

import pytest

from tokeo.core.utils.memfd import memory_paths, MemfdUnavailable


def test_single_blob_is_readable():
    with memory_paths(b'hello world') as (path,):
        with open(path, 'rb') as f:
            assert f.read() == b'hello world'


def test_multiple_blobs_are_independent():
    with memory_paths(b'first', b'second') as (a, b):
        assert a != b
        assert open(a, 'rb').read() == b'first'
        assert open(b, 'rb').read() == b'second'


def test_str_is_encoded_utf8():
    with memory_paths('grüße') as (path,):
        assert open(path, 'rb').read() == 'grüße'.encode('utf-8')


def test_no_blobs_yields_empty():
    with memory_paths() as paths:
        assert paths == ()


def _self_signed_pem():
    # generated in-process with cryptography, the same library the tls module
    # uses for its self-signed test certificate (no external openssl needed)
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def test_loads_ssl_cert_chain_from_memory():
    cert_pem, key_pem = _self_signed_pem()
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    with memory_paths(cert_pem, key_pem) as (cert_ref, key_ref):
        context.load_cert_chain(cert_ref, key_ref)      # must not raise


@pytest.mark.skipif(not os.path.isdir('/dev/fd'), reason='needs /dev/fd')
def test_devfd_pipe_backing_when_no_memfd(monkeypatch):
    # simulate macOS/BSD: no memfd_create, so the /dev/fd pipe path is taken
    monkeypatch.delattr(os, 'memfd_create', raising=False)
    with memory_paths(b'over a pipe') as (path,):
        assert path.startswith('/dev/fd/')
        assert open(path, 'rb').read() == b'over a pipe'


def test_raises_when_no_backing(monkeypatch):
    # simulate a platform with neither memfd_create nor /dev/fd (e.g. Windows)
    monkeypatch.delattr(os, 'memfd_create', raising=False)
    monkeypatch.setattr(os.path, 'isdir', lambda p: False)
    with pytest.raises(MemfdUnavailable):
        with memory_paths(b'x'):
            pass
