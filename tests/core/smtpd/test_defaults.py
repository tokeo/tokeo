"""
Ported from the reference suite's test/specs/defaults_test.rb.

Checks the default properties and the host-validation exceptions of the server.
"""

import pytest

from tokeo.core.smtpd.server import (
    SmtpdServer,
    CrlfMode,
    CrlfDataMode,
    AuthMode,
    EncryptMode,
    DEFAULT_SMTPD_HOST,
    DEFAULT_SMTPD_PORT,
    DEFAULT_IO_CMD_TIMEOUT,
    DEFAULT_IO_PROXY_CMD_TIMEOUT,
    DEFAULT_IO_SESSION_TIMEOUT,
    DEFAULT_IO_BUFFER_MAX_SIZE,
)
from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents


def _smtpd(**kwargs):
    return SmtpdServer(CaptureSmtpdEvents(), **kwargs)


def test_exception_on_empty_hosts():
    """Empty hosts raise ValueError "No hosts defined!"."""
    with pytest.raises(ValueError, match='No hosts defined!'):
        SmtpdServer(CaptureSmtpdEvents(), ports=2525, hosts='')


def test_exception_on_empty_host_in_hosts():
    """An empty entry in the hosts raises ValueError."""
    with pytest.raises(ValueError, match='Detected an empty identifier in given hosts!'):
        SmtpdServer(CaptureSmtpdEvents(), ports=2525, hosts='1.2.3.4,,5.6.7.8')


def test_must_not_respond_to_port():
    """The server has no attribute port, only ports."""
    assert not hasattr(_smtpd(), 'port')


def test_must_not_respond_to_host():
    """The server has no attribute host, only hosts."""
    assert not hasattr(_smtpd(), 'host')


def test_defaults_ports():
    """The default ports are ["2525"]."""
    assert _smtpd().ports == [str(DEFAULT_SMTPD_PORT)]


def test_defaults_hosts():
    """The default hosts are ["127.0.0.1"]."""
    assert _smtpd().hosts == [DEFAULT_SMTPD_HOST]


def test_defaults_addresses():
    """The default addresses are ["127.0.0.1:2525"]."""
    assert _smtpd().addresses == [f'{DEFAULT_SMTPD_HOST}:{DEFAULT_SMTPD_PORT}']


def test_defaults_max_processings():
    """max_processings defaults to 4."""
    assert _smtpd().max_processings == 4


def test_defaults_max_connections():
    """max_connections defaults to None, no limit."""
    assert _smtpd().max_connections is None


def test_defaults_crlf_mode():
    """crlf_mode defaults to CRLF_ENSURE."""
    assert _smtpd().crlf_mode is CrlfMode.CRLF_ENSURE


def test_defaults_crlf_data_mode():
    """crlf_data_mode defaults to CRLF_DATA_MODERATE, with CRLF_STRICT to STRICT."""
    assert _smtpd().crlf_data_mode is CrlfDataMode.CRLF_DATA_MODERATE
    assert _smtpd(settings={'crlf_mode': 'CRLF_LEAVE'}).crlf_data_mode is CrlfDataMode.CRLF_DATA_MODERATE
    assert _smtpd(settings={'crlf_mode': 'CRLF_STRICT'}).crlf_data_mode is CrlfDataMode.CRLF_DATA_STRICT


@pytest.mark.parametrize('value', ['CRLF_DATA_MODERATE', 'CRLF_DATA_UNSAFE'])
def test_crlf_strict_allows_only_the_strict_data_modes(value):
    """CRLF_STRICT with a not strict crlf_data_mode is rejected."""
    with pytest.raises(ValueError, match='crlf_data_mode'):
        _smtpd(settings={'crlf_mode': 'CRLF_STRICT', 'crlf_data_mode': value})
    smtpd = _smtpd(settings={'crlf_mode': 'CRLF_STRICT', 'crlf_data_mode': 'CRLF_DATA_STRICT_CLOSE'})
    assert smtpd.crlf_data_mode is CrlfDataMode.CRLF_DATA_STRICT_CLOSE


def test_unknown_crlf_data_mode_is_rejected():
    """An unknown crlf_data_mode names the allowed values."""
    with pytest.raises(ValueError, match='crlf_data_mode must be one of'):
        _smtpd(settings={'crlf_data_mode': 'CRLF_DATA_LAX'})


def test_defaults_io_cmd_timeout():
    """io_cmd_timeout defaults to 30 seconds."""
    assert _smtpd().io_cmd_timeout == DEFAULT_IO_CMD_TIMEOUT


def test_defaults_io_proxy_cmd_timeout():
    """io_proxy_cmd_timeout defaults to 5 seconds."""
    assert _smtpd().io_proxy_cmd_timeout == DEFAULT_IO_PROXY_CMD_TIMEOUT


@pytest.mark.parametrize('name', ['io_cmd_timeout', 'io_proxy_cmd_timeout'])
@pytest.mark.parametrize('value', [0, 0.5, -1, None, True, '30'])
def test_io_timeouts_below_one_second_are_rejected(name, value):
    """An io timeout below 1 second, null or not a number stops the server setup."""
    with pytest.raises(ValueError, match=name):
        _smtpd(settings={name: value})


def test_defaults_io_session_timeout():
    """io_session_timeout defaults to 3600 seconds, also when set to None."""
    assert DEFAULT_IO_SESSION_TIMEOUT == 3600
    assert _smtpd().io_session_timeout == DEFAULT_IO_SESSION_TIMEOUT
    assert _smtpd(settings={'io_session_timeout': None}).io_session_timeout == DEFAULT_IO_SESSION_TIMEOUT


@pytest.mark.parametrize('value', [1, 1.5, 86400])
def test_io_session_timeout_takes_seconds_from_1_to_86400(value):
    """io_session_timeout takes an int or a float from 1 to 86400 seconds."""
    assert _smtpd(settings={'io_session_timeout': value}).io_session_timeout == value


@pytest.mark.parametrize('value', [0, 0.5, -1, 86401, True, '10'])
def test_io_session_timeout_out_of_range_is_rejected(value):
    """io_session_timeout outside 1 to 86400 or not a number stops the setup."""
    with pytest.raises(ValueError, match='io_session_timeout'):
        _smtpd(settings={'io_session_timeout': value})


def test_defaults_io_buffer_max_size():
    """io_buffer_max_size defaults to 1 MiB."""
    assert _smtpd().io_buffer_max_size == DEFAULT_IO_BUFFER_MAX_SIZE


def test_defaults_do_dns_reverse_lookup():
    """do_dns_reverse_lookup defaults to True."""
    assert _smtpd().do_dns_reverse_lookup is True


def test_defaults_auth_mode():
    """auth_mode defaults to AUTH_FORBIDDEN."""
    assert _smtpd().auth_mode is AuthMode.AUTH_FORBIDDEN


def test_defaults_trusted_relay_auth_ids():
    """trusted_relay_auth_ids defaults to an empty list."""
    assert _smtpd().trusted_relay_auth_ids == []


@pytest.mark.parametrize('value', [None, 'relay', ('relay',), ['relay', 1], ['relay', ''], [' relay'], ['relay '], ['  ']])
def test_trusted_relay_auth_ids_not_a_list_of_ids_is_rejected(value):
    """trusted_relay_auth_ids accepts only a list of stripped non-empty ids."""
    with pytest.raises(ValueError, match='trusted_relay_auth_ids'):
        _smtpd(settings={'trusted_relay_auth_ids': value})


def test_defaults_encrypt_mode():
    """encrypt_mode defaults to TLS_FORBIDDEN."""
    assert _smtpd().encrypt_mode is EncryptMode.TLS_FORBIDDEN


def test_defaults_proxy_extension():
    """proxy_extension defaults to False."""
    assert _smtpd().proxy_extension is False


def test_defaults_pipelining_extension():
    """pipelining_extension defaults to False."""
    assert _smtpd().pipelining_extension is False


def test_defaults_internationalization_extensions():
    """internationalization_extensions defaults to False."""
    assert _smtpd().internationalization_extensions is False


@pytest.mark.parametrize(
    'settings, name',
    [
        ({'max_processings': 0}, 'max_processings'),
        ({'max_processings': '4'}, 'max_processings'),
        ({'max_connections': 0}, 'max_connections'),
        ({'max_connections': 2, 'max_processings': 4}, 'max_connections'),
    ],
)
def test_limits_that_do_not_fit_are_rejected(settings, name):
    """A limit that is no positive integer, or too low, stops the setup."""
    with pytest.raises(ValueError, match=name):
        _smtpd(settings=settings)


def test_keyword_style_settings_merge_into_the_settings():
    """Keyword settings apply, tls_mode sets encrypt_mode, the settings win."""
    smtpd = _smtpd(
        settings={'io_cmd_timeout': 20}, io_cmd_timeout=10, io_session_timeout=60, tls_mode='TLS_OPTIONAL', tls_cert_cn='localhost'
    )
    assert smtpd.encrypt_mode is EncryptMode.TLS_OPTIONAL
    assert smtpd.io_cmd_timeout == 20
    assert smtpd.io_session_timeout == 60
