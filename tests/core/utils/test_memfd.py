"""
Tests for tokeo.core.utils.memfd, the RAM-backed paths: a single blob yields a
readable path, many blobs yield independent paths, str is encoded, and the
real consumer works (ssl.SSLContext.load_cert_chain from PEM in memory). The
platform branches are exercised too: forcing the /dev/fd pipe path (as on
macOS) and raising when the platform offers neither backing (as on Windows).
"""

import os
import ssl

import pytest

from tokeo.core.utils.memfd import open_memory_paths, TokeoMemfdError
from tokeo.core.utils.tls import create_certificate

needs_devfd = pytest.mark.skipif(not os.path.isdir('/dev/fd'), reason='needs /dev/fd')


def test_single_blob_is_readable():
    """The path of one blob reads back its bytes."""
    with open_memory_paths(b'hello world') as (path,):
        with open(path, 'rb') as f:
            assert f.read() == b'hello world'


@pytest.mark.skipif(not hasattr(os, 'memfd_create'), reason='memfd_create is linux only')
def test_memory_file_is_closed_on_exec():
    """The fd of the blob is not inherited by a program started meanwhile."""
    with open_memory_paths(b'key') as (path,):
        fd = int(os.path.basename(path))
        assert os.get_inheritable(fd) is False


def test_multiple_blobs_are_independent():
    """Each blob gets its own path with its own bytes."""
    with open_memory_paths(b'first', b'second') as (a, b):
        assert a != b
        with open(a, 'rb') as f:
            assert f.read() == b'first'
        with open(b, 'rb') as f:
            assert f.read() == b'second'


def test_str_is_encoded_utf8():
    """A str blob reads back as its utf-8 bytes."""
    with open_memory_paths('grüße') as (path,):
        with open(path, 'rb') as f:
            assert f.read() == 'grüße'.encode('utf-8')


def test_no_blobs_yields_empty():
    """Without blobs open_memory_paths yields an empty tuple."""
    with open_memory_paths() as paths:
        assert paths == ()


def test_loads_ssl_cert_chain_from_memory():
    """ssl loads a certificate and key from the paths in memory."""
    _, _, cert_pem, key_pem = create_certificate('localhost')
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    with open_memory_paths(cert_pem, key_pem) as (cert_ref, key_ref):
        context.load_cert_chain(cert_ref, key_ref)  # must not raise


@needs_devfd
def test_devfd_pipe_backing_when_no_memfd(monkeypatch):
    """Without memfd_create, as on macOS, the paths are /dev/fd pipes."""
    monkeypatch.delattr(os, 'memfd_create', raising=False)
    with open_memory_paths(b'over a pipe') as (path,):
        assert path.startswith('/dev/fd/')
        with open(path, 'rb') as f:
            assert f.read() == b'over a pipe'


def test_raises_when_no_backing(monkeypatch):
    """Without memfd_create and /dev/fd, as on Windows, it raises."""
    monkeypatch.delattr(os, 'memfd_create', raising=False)
    monkeypatch.setattr(os.path, 'isdir', lambda p: False)
    with pytest.raises(TokeoMemfdError):
        with open_memory_paths(b'x'):
            pass
