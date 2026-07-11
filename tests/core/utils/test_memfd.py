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
import subprocess
import tempfile

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


def test_loads_ssl_cert_chain_from_memory():
    d = tempfile.mkdtemp(prefix='tokeo-memfd-')
    crt, key = os.path.join(d, 'c.pem'), os.path.join(d, 'k.pem')
    subprocess.run(
        ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-keyout', key,
         '-out', crt, '-days', '1', '-nodes', '-subj', '/CN=localhost'],
        check=True, capture_output=True,
    )
    cert_pem, key_pem = open(crt).read(), open(key).read()
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
