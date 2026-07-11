"""
Ported from the reference suite's test/unit/process_proxy_test.rb.

Checks the PROXY protocol handling in ```process_line```: valid TCP4/TCP6
(with IPv6 and port normalization), an illegal command, a repeated PROXY, and
an out-of-sequence PROXY.
"""

import pytest

from tokeo.core.smtpd.server import SmtpdServer, SmtpdSession
from tokeo.core.smtpd.exc import Smtpd421Exception, Smtpd503Exception
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_helpers import run_line


_SMTPD = SmtpdServer(CaptureSmtpdEvents(), settings={
    'max_processings': 1,
    'auth_mode': 'AUTH_OPTIONAL',
    'encrypt_mode': 'TLS_OPTIONAL',
    'tls_cert_cn': 'localhost',
    'proxy_extension': True,
    'pipelining_extension': False,
    'internationalization_extensions': True,
})


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
    session = _session()
    run_line(_SMTPD, session, 'PROXY TCP4 1.1.1.1 2.2.2.2 1111 2222')
    proxy = session.ctx.server.proxy
    assert proxy is not None
    assert proxy['source_ip'] == '1.1.1.1'
    assert proxy['source_port'] == 1111
    assert proxy['dest_ip'] == '2.2.2.2'
    assert proxy['dest_port'] == 2222


def test_01_proxy_tcp6_valid():
    session = _session()
    run_line(_SMTPD, session, 'PROXY TCP6 003::0003 4::4 000003333 4444')
    proxy = session.ctx.server.proxy
    assert proxy is not None
    assert proxy['source_ip'] == '3::3'
    assert proxy['source_port'] == 3333
    assert proxy['dest_ip'] == '4::4'
    assert proxy['dest_port'] == 4444


def test_02_proxy_illegal_command():
    session = _session()
    with pytest.raises(Smtpd421Exception):
        run_line(_SMTPD, session, 'PROXY ILLEGAL COMMAND')


def test_03_proxy_multiple_proxy_abort():
    session = _session()
    run_line(_SMTPD, session, 'PROXY TCP4 1.1.1.1 2.2.2.2 1111 2222')
    with pytest.raises(Smtpd421Exception):
        run_line(_SMTPD, session, 'PROXY TCP6 003::0003 4::4 000003333 4444')


def test_04_proxy_illegal_sequence():
    session = _session()
    run_line(_SMTPD, session, 'EHLO SEQUENCE')
    with pytest.raises(Smtpd503Exception):
        run_line(_SMTPD, session, 'PROXY UNKNOWN TEST')
