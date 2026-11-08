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

from tokeo.core.smtpd.server import SmtpdServer, _CMD_RSET, _CMD_QUIT, _SmtpdSession
from tokeo.core.smtpd.exc import Smtpd535Exception
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
            options={
                'max_processings': 1,
                'auth_mode': 'AUTH_OPTIONAL',
                'encrypt_mode': 'TLS_OPTIONAL',
                'tls_cert_cn': 'localhost',
                'pipelining_extension': False,
                'internationalization_extensions': True,
            },
        )
        self.session = _SmtpdSession()
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

    def send_line(self, line, brk=b'\r\n'):
        return run_line(self.smtpd, self.session, line, brk)


@pytest.fixture(scope='module')
def shared_session():
    """One session for the module, each test continues the dialog before."""
    return ProcessLineSession()


def test_10_ehlo(shared_session):
    """EHLO lists the extensions with AUTH and STARTTLS and stores the name."""
    result = shared_session.send_line('EHLO Process line unit test')
    assert result == ('250-Process line test - Greeting\r\n' '250-8BITMIME\r\n250-SMTPUTF8\r\n' '250-AUTH LOGIN PLAIN\r\n250 STARTTLS')
    assert shared_session.session.ctx.server.helo == 'Process line unit test'


def test_11_ehlo_again_resets_like_rset(shared_session):
    """A second EHLO answers again, resets like RSET and keeps the name."""
    result = shared_session.send_line('EHLO Process line unit test')
    assert result.endswith('250 STARTTLS')
    assert shared_session.session.ctx.server.helo == 'Process line unit test'


def test_20_auth_login_simulate_fail(shared_session):
    """AUTH LOGIN with a wrong password raises 535 and stays unauthenticated."""
    result = shared_session.send_line('AUTH LOGIN')
    assert result == '334 ' + base64.b64encode(b'Username:').decode()
    result = shared_session.send_line(base64.b64encode(b'administrator').decode())
    assert result == '334 ' + base64.b64encode(b'Password:').decode()
    with pytest.raises(Smtpd535Exception):
        shared_session.send_line(base64.b64encode(b'error_password').decode())
    events = shared_session.smtpd.events_handler
    assert events.ev_auth_authentication_id == 'administrator'
    assert events.ev_auth_authentication == 'error_password'
    server = shared_session.session.ctx.server
    assert server.authorization_id == ''
    assert server.authentication_id == ''
    assert server.authenticated_at is None


def test_21_auth_plain_authenticate_supervisor(shared_session):
    """AUTH PLAIN as administrator answers 235, authorized as supervisor."""
    result = shared_session.send_line('AUTH PLAIN')
    assert result == '334 '
    result = shared_session.send_line('AGFkbWluaXN0cmF0b3IAcGFzc3dvcmQ')
    assert result == '235 OK'
    events = shared_session.smtpd.events_handler
    assert events.ev_auth_authentication_id == 'administrator'
    assert events.ev_auth_authentication == 'password'
    server = shared_session.session.ctx.server
    assert server.authorization_id == 'supervisor'
    assert server.authentication_id == 'administrator'
    assert server.authenticated_at is not None


def test_30_mail_from(shared_session):
    """MAIL FROM answers 250 and stores the sender."""
    result = shared_session.send_line('MAIL FROM: demo@local.local')
    assert result == '250 OK'
    assert shared_session.session.ctx.envelope.mail_from == 'demo@local.local'


def test_40_rcpt_to(shared_session):
    """Two RCPT TO answer 250 and store both recipients in order."""
    assert shared_session.send_line('RCPT TO: demo1@local.local') == '250 OK'
    assert shared_session.send_line('RCPT TO: demo2@local.local') == '250 OK'
    rcpts = shared_session.session.ctx.envelope.rcpt_tos
    assert len(rcpts) == 2
    assert rcpts[0] == 'demo1@local.local'
    assert rcpts[1] == 'demo2@local.local'


def test_50_data(shared_session):
    """DATA delivers 174 bytes with the injected headers, then resets."""
    assert shared_session.send_line('DATA').startswith('354 ')
    shared_session.send_line('From: <demo@local.local>')
    shared_session.send_line('To: <demo1@local.local>, <demo2@local.local>')
    shared_session.send_line('Subject: Unit Test')
    shared_session.send_line('X-test: 1')
    shared_session.send_line('')
    shared_session.send_line('Welcome to message!')
    shared_session.send_line('Have fun.')
    shared_session.send_line('..')
    result = shared_session.send_line('.')
    assert result.startswith('250 ')
    assert shared_session.session.cmd_sequence is _CMD_RSET
    assert shared_session.session.ctx.message.bytesize == -1
    assert bytes(shared_session.session.ctx.message.data) == b''
    events = shared_session.smtpd.events_handler
    assert events.ev_message_bytesize == 174
    assert events.ev_message_data.startswith(b'Received: test header\r\n')
    message = email.message_from_bytes(events.ev_message_data)
    assert 'demo@local.local' in message['From']
    assert message['Subject'] == 'Unit Test'
    assert message['Received'] == 'test header'
    assert int(message['X-test']) == 1
    assert message['X-inject'] == 'Y'


def test_90_noop(shared_session):
    """NOOP answers "250 OK"."""
    assert shared_session.send_line('NOOP') == '250 OK'


def test_91_rset(shared_session):
    """RSET answers "250 OK" and sets the RSET state."""
    assert shared_session.send_line('RSET') == '250 OK'
    assert shared_session.session.cmd_sequence is _CMD_RSET


def test_99_quit(shared_session):
    """QUIT answers empty and sets the QUIT state."""
    assert shared_session.send_line('QUIT') == ''
    assert shared_session.session.cmd_sequence is _CMD_QUIT
