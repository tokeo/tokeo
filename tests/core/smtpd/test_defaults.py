"""
Ported from the reference suite's test/specs/defaults_test.rb.

Checks the default properties and the host-validation exceptions of the server.
"""

import pytest

from tokeo.core.smtpd.server import (
    SmtpdServer,
    CrlfMode,
    AuthMode,
    EncryptMode,
    DEFAULT_SMTPD_HOST,
    DEFAULT_SMTPD_PORT,
    DEFAULT_IO_WAITREADABLE_SLEEP,
    DEFAULT_IO_CMD_TIMEOUT,
    DEFAULT_IO_BUFFER_CHUNK_SIZE,
    DEFAULT_IO_BUFFER_MAX_SIZE,
)
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents


def _smtpd(**kwargs):
    return SmtpdServer(CaptureSmtpdEvents(), **kwargs)


def test_exception_on_empty_hosts():
    with pytest.raises(ValueError, match='No hosts defined!'):
        SmtpdServer(CaptureSmtpdEvents(), ports=2525, hosts='')


def test_exception_on_empty_host_in_hosts():
    with pytest.raises(ValueError, match='Detected an empty identifier in given hosts!'):
        SmtpdServer(CaptureSmtpdEvents(), ports=2525, hosts='1.2.3.4,,5.6.7.8')


def test_must_not_respond_to_port():
    assert not hasattr(_smtpd(), 'port')


def test_must_not_respond_to_host():
    assert not hasattr(_smtpd(), 'host')


def test_defaults_ports():
    assert _smtpd().ports == [str(DEFAULT_SMTPD_PORT)]


def test_defaults_hosts():
    assert _smtpd().hosts == [DEFAULT_SMTPD_HOST]


def test_defaults_addresses():
    assert _smtpd().addresses == [f'{DEFAULT_SMTPD_HOST}:{DEFAULT_SMTPD_PORT}']


def test_defaults_max_processings():
    assert _smtpd().max_processings == 4


def test_defaults_max_connections():
    assert _smtpd().max_connections is None


def test_defaults_crlf_mode():
    assert _smtpd().crlf_mode is CrlfMode.CRLF_ENSURE


def test_defaults_io_waitreadable_sleep():
    assert _smtpd().io_waitreadable_sleep == DEFAULT_IO_WAITREADABLE_SLEEP


def test_defaults_io_cmd_timeout():
    assert _smtpd().io_cmd_timeout == DEFAULT_IO_CMD_TIMEOUT


def test_defaults_io_buffer_chunk_size():
    assert _smtpd().io_buffer_chunk_size == DEFAULT_IO_BUFFER_CHUNK_SIZE


def test_defaults_io_buffer_max_size():
    assert _smtpd().io_buffer_max_size == DEFAULT_IO_BUFFER_MAX_SIZE


def test_defaults_do_dns_reverse_lookup():
    assert _smtpd().do_dns_reverse_lookup is True


def test_defaults_auth_mode():
    assert _smtpd().auth_mode is AuthMode.AUTH_FORBIDDEN


def test_defaults_encrypt_mode():
    assert _smtpd().encrypt_mode is EncryptMode.TLS_FORBIDDEN


def test_defaults_proxy_extension():
    assert _smtpd().proxy_extension is False


def test_defaults_pipelining_extension():
    assert _smtpd().pipelining_extension is False


def test_defaults_internationalization_extensions():
    assert _smtpd().internationalization_extensions is False
