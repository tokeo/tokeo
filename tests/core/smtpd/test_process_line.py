"""
Ported from the reference suite's test/unit/process_line_test.rb.

Drives ```process_line``` directly (no TCP) through one ordered dialog on a
shared session: EHLO, AUTH LOGIN (fail) and AUTH PLAIN (supervisor), MAIL, RCPT,
DATA (with a Received header injected at data start and an X-inject header at the
header boundary), NOOP, RSET, QUIT. The tests are order dependent, exactly like
the reference suite.
"""

# test rules exception: numbered test names mirror the midi-smtp-server suite

import email
import base64

import pytest

from tokeo.core.smtpd.server import SmtpdServer, CMD_RSET, CMD_QUIT, SmtpdSession
from tokeo.core.smtpd.exc import Smtpd503Exception, Smtpd535Exception
from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_tests_helper import run_line


class ProcessLineEvents(CaptureSmtpdEvents):
    """Injects a Received header at data start and X-inject at the boundary."""

    def on_message_data_start_event(self, ctx):
        ctx.message.data += b'Received: test header' + ctx.message.crlf

    def on_message_data_headers_event(self, ctx):
        ctx.message.data += b'X-inject: Y' + ctx.message.crlf


class ProcessLineSession:
    """One SMTP session, shared in order by all tests of this file."""

    def __init__(self):
        self.smtpd = SmtpdServer(
            ProcessLineEvents(),
            settings={
                'max_processings': 1,
                'auth_mode': 'AUTH_OPTIONAL',
                'encrypt_mode': 'TLS_OPTIONAL',
                'tls_cert_cn': 'localhost',
                'pipelining_extension': False,
                'internationalization_extensions': True,
            },
        )
        self.session = SmtpdSession()
        self.smtpd.process_reset_session(self.session, connection_initialize=True)
        server = self.session.ctx.server
        server.local_host = 'localhost.local'
        server.local_ip = '127.0.0.1'
        server.local_port = '2525'
        server.local_response = 'Process line test - Welcome'
        server.remote_host = 'localhost'
        server.remote_ip = '127.0.0.1'
        server.remote_port = '65534'
        server.helo_response = 'Process line test - Greeting'

    def line(self, line, brk=b'\r\n'):
        return run_line(self.smtpd, self.session, line, brk)


@pytest.fixture(scope='module')
def shared():
    """One session for the module, each test continues the dialog before."""
    return ProcessLineSession()


def test_10_ehlo(shared):
    """EHLO lists the extensions with AUTH and STARTTLS and stores the name."""
    result = shared.line('EHLO Process line unit test')
    assert result == (
        '250-Process line test - Greeting\r\n' '250-8BITMIME\r\n250-SMTPUTF8\r\n' '250-AUTH LOGIN PLAIN\r\n250-STARTTLS\r\n250 OK'
    )
    assert shared.session.ctx.server.helo == 'Process line unit test'


def test_11_ehlo_bad_sequence(shared):
    """A second EHLO raises 503."""
    with pytest.raises(Smtpd503Exception):
        shared.line('EHLO Process line unit test')


def test_20_auth_login_simulate_fail(shared):
    """AUTH LOGIN with a wrong password raises 535 and stays unauthenticated."""
    result = shared.line('AUTH LOGIN')
    assert result == '334 ' + base64.b64encode(b'Username:').decode()
    result = shared.line(base64.b64encode(b'administrator').decode())
    assert result == '334 ' + base64.b64encode(b'Password:').decode()
    with pytest.raises(Smtpd535Exception):
        shared.line(base64.b64encode(b'error_password').decode())
    events = shared.smtpd.events_handler
    assert events.ev_auth_authentication_id == 'administrator'
    assert events.ev_auth_authentication == 'error_password'
    server = shared.session.ctx.server
    assert server.authorization_id == ''
    assert server.authentication_id == ''
    assert str(server.authenticated) == ''


def test_21_auth_plain_authenticate_supervisor(shared):
    """AUTH PLAIN as administrator answers 235, authorized as supervisor."""
    result = shared.line('AUTH PLAIN')
    assert result == '334 '
    result = shared.line('AGFkbWluaXN0cmF0b3IAcGFzc3dvcmQ')
    assert result == '235 OK'
    events = shared.smtpd.events_handler
    assert events.ev_auth_authentication_id == 'administrator'
    assert events.ev_auth_authentication == 'password'
    server = shared.session.ctx.server
    assert server.authorization_id == 'supervisor'
    assert server.authentication_id == 'administrator'
    assert str(server.authenticated) != ''


def test_30_mail_from(shared):
    """MAIL FROM answers 250 and stores the sender."""
    result = shared.line('MAIL FROM: demo@local.local')
    assert result == '250 OK'
    assert shared.session.ctx.envelope.mail_from == 'demo@local.local'


def test_40_rcpt_to(shared):
    """Two RCPT TO answer 250 and store both recipients in order."""
    assert shared.line('RCPT TO: demo1@local.local') == '250 OK'
    assert shared.line('RCPT TO: demo2@local.local') == '250 OK'
    rcpts = shared.session.ctx.envelope.rcpt_tos
    assert len(rcpts) == 2
    assert rcpts[0] == 'demo1@local.local'
    assert rcpts[1] == 'demo2@local.local'


def test_50_data(shared):
    """DATA delivers 174 bytes with the injected headers, then resets."""
    assert shared.line('DATA').startswith('354 ')
    shared.line('From: <demo@local.local>')
    shared.line('To: <demo1@local.local>, <demo2@local.local>')
    shared.line('Subject: Unit Test')
    shared.line('X-test: 1')
    shared.line('')
    shared.line('Welcome to message!')
    shared.line('Have fun.')
    shared.line('..')
    result = shared.line('.')
    assert result.startswith('250 ')
    assert shared.session.cmd_sequence is CMD_RSET
    assert shared.session.ctx.message.bytesize == -1
    assert bytes(shared.session.ctx.message.data) == b''
    events = shared.smtpd.events_handler
    assert events.ev_message_bytesize == 174
    assert events.ev_message_data.startswith(b'Received: test header\r\n')
    message = email.message_from_bytes(events.ev_message_data)
    assert 'demo@local.local' in message['From']
    assert message['Subject'] == 'Unit Test'
    assert message['Received'] == 'test header'
    assert int(message['X-test']) == 1
    assert message['X-inject'] == 'Y'


def test_90_noop(shared):
    """NOOP answers "250 OK"."""
    assert shared.line('NOOP') == '250 OK'


def test_91_rset(shared):
    """RSET answers "250 OK" and sets the RSET state."""
    assert shared.line('RSET') == '250 OK'
    assert shared.session.cmd_sequence is CMD_RSET


def test_99_quit(shared):
    """QUIT answers empty and sets the QUIT state."""
    assert shared.line('QUIT') == ''
    assert shared.session.cmd_sequence is CMD_QUIT
