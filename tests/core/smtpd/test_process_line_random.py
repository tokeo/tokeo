"""
Ported from midi-smtp-server test/unit/process_line_random_test.rb.

Checks HELO/EHLO case-insensitivity and argument stripping, and the EHLO
extension advertisement with pipelining on and TLS forbidden.
"""

from tokeo.core.smtpd.server import SmtpdServer, SmtpdSession
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_helpers import run_line


_SMTPD = SmtpdServer(CaptureSmtpdEvents(), settings={
    'max_processings': 1,
    'auth_mode': 'AUTH_OPTIONAL',
    'encrypt_mode': 'TLS_FORBIDDEN',
    'pipelining_extension': True,
    'internationalization_extensions': True,
})


def _session():
    session = SmtpdSession()
    _SMTPD.process_reset_session(session, connection_initialize=True)
    session.ctx.server.helo_response = 'Process line test - Greeting'
    return session


def test_helo():
    session = _session()
    result = run_line(_SMTPD, session, 'HELO Process line unit test')
    assert result == '250 OK Process line test - Greeting'
    assert session.ctx.server.helo == 'Process line unit test'


def test_helo_no_case_strip():
    session = _session()
    helo_str = '  Process line unit test   '
    result = run_line(_SMTPD, session, f'hElO {helo_str}')
    assert result == '250 OK Process line test - Greeting'
    assert session.ctx.server.helo == helo_str.strip()


def test_ehlo_no_case_strip():
    session = _session()
    helo_str = '  Process line unit test   '
    result = run_line(_SMTPD, session, f'eHlO {helo_str}')
    assert result == (
        '250-Process line test - Greeting\r\n'
        '250-8BITMIME\r\n250-SMTPUTF8\r\n250-PIPELINING\r\n'
        '250-AUTH LOGIN PLAIN\r\n250 OK'
    )
    assert session.ctx.server.helo == helo_str.strip()
