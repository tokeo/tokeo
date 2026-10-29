"""
Ported from the reference suite's test/unit/process_proxy_test.rb.

Checks the PROXY protocol handling in ```process_line```: valid TCP4/TCP6
(with IPv6 normalization), an illegal command, a repeated PROXY, and a first
line that is not PROXY. Unlike the reference, the PROXY header is
mandatory as the first line, so that case aborts with 421 instead of 503.
"""

# test rules exception: numbered test names mirror the midi-smtp-server suite

import pytest

from tokeo.core.smtpd.server import SmtpdServer, SmtpdSession
from tokeo.core.smtpd.exc import Smtpd421CloseException
from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_tests_helper import run_line

_SMTPD = SmtpdServer(
    CaptureSmtpdEvents(),
    settings={
        'max_processings': 1,
        'auth_mode': 'AUTH_OPTIONAL',
        'encrypt_mode': 'TLS_OPTIONAL',
        'tls_cert_cn': 'localhost',
        'proxy_extension': True,
        'pipelining_extension': False,
        'internationalization_extensions': True,
    },
)


def _session():
    session = SmtpdSession()
    _SMTPD.process_reset_session(session, connection_initialize=True)
    server = session.ctx.server
    server.local_host = 'localhost.local'
    server.local_ip = '127.0.0.1'
    server.local_port = '2525'
    server.remote_host = 'localhost'
    server.remote_ip = '127.0.0.1'
    server.remote_port = '65534'
    server.helo_response = 'Process line test - Greeting'
    return session


def test_00_proxy_tcp4_valid():
    """A TCP4 PROXY line fills source and dest ip and port."""
    session = _session()
    run_line(_SMTPD, session, 'PROXY TCP4 1.1.1.1 2.2.2.2 1111 2222')
    proxy = session.ctx.server.proxy
    assert proxy is not None
    assert proxy['source_ip'] == '1.1.1.1'
    assert proxy['source_port'] == 1111
    assert proxy['dest_ip'] == '2.2.2.2'
    assert proxy['dest_port'] == 2222


def test_01_proxy_tcp6_valid():
    """A TCP6 PROXY line is stored with normalized ips."""
    session = _session()
    run_line(_SMTPD, session, 'PROXY TCP6 003::0003 4::4 3333 4444')
    proxy = session.ctx.server.proxy
    assert proxy is not None
    assert proxy['source_ip'] == '3::3'
    assert proxy['source_port'] == 3333
    assert proxy['dest_ip'] == '4::4'
    assert proxy['dest_port'] == 4444


def test_02_proxy_illegal_command():
    """An illegal PROXY line raises the 421 abort."""
    session = _session()
    with pytest.raises(Smtpd421CloseException):
        run_line(_SMTPD, session, 'PROXY ILLEGAL COMMAND')


def test_03_proxy_multiple_proxy_abort():
    """A second PROXY line raises the 421 abort."""
    session = _session()
    run_line(_SMTPD, session, 'PROXY TCP4 1.1.1.1 2.2.2.2 1111 2222')
    with pytest.raises(Smtpd421CloseException):
        run_line(_SMTPD, session, 'PROXY TCP6 003::0003 4::4 000003333 4444')


def test_04_proxy_not_first_aborts():
    """A first line other than PROXY raises the 421 abort."""
    session = _session()
    with pytest.raises(Smtpd421CloseException):
        run_line(_SMTPD, session, 'EHLO SEQUENCE')


def test_proxy_port_zero_is_valid():
    """A port 0 is valid (haproxy proxy-protocol version 1)."""
    session = _session()
    run_line(_SMTPD, session, 'PROXY TCP4 1.1.1.1 2.2.2.2 0 25')
    assert session.ctx.server.proxy['source_port'] == 0


@pytest.mark.parametrize(
    'line',
    [
        'PROXY TCP4 1.1.1.1 2.2.2.2 01111 2222',
        'PROXY TCP4 1.1.1.1 2.2.2.2 65536 2222',
        'PROXY  TCP4 1.1.1.1 2.2.2.2 1111 2222',
        'PROXY TCP4 1.1.1.1  2.2.2.2 1111 2222',
        'PROXY UNKNOWN ' + 'x' * 100,
    ],
)
def test_proxy_outside_the_specification_aborts(line):
    """Leading zeros, ports above 65535, two spaces or over 107 bytes abort."""
    session = _session()
    with pytest.raises(Smtpd421CloseException):
        run_line(_SMTPD, session, line)


def test_proxy_in_lower_case_is_accepted():
    """The PROXY header is accepted in any case."""
    session = _session()
    run_line(_SMTPD, session, 'proxy tcp4 1.1.1.1 2.2.2.2 1111 2222')
    assert session.ctx.server.proxy['proto'] == 'TCP4'
