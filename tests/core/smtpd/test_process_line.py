"""
Ported from the reference suite's test/unit/process_line_test.rb.

Drives ```process_line``` directly (no TCP) through one ordered dialog on a
shared session: EHLO, AUTH LOGIN (fail) and AUTH PLAIN (supervisor), MAIL, RCPT,
DATA (with a Received header injected at data start and an X-inject header at the
header boundary), NOOP, RSET, QUIT. The tests are order dependent, exactly like
the reference suite.
"""

import email
import base64

import pytest

from tokeo.core.smtpd.server import SmtpdServer, CMD_RSET, CMD_QUIT, SmtpdSession
from tokeo.core.smtpd.exc import Smtpd503Exception, Smtpd535Exception
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_helpers import run_line


class ProcessLineEvents(CaptureSmtpdEvents):
    """Injects a Received header at data start and X-inject at the boundary."""

    def on_message_data_start_event(self, ctx):
        ctx.message.data += b'Received: test header' + ctx.message.crlf

    def on_message_data_headers_event(self, ctx):
        ctx.message.data += b'X-inject: Y' + ctx.message.crlf


class TestProcessLine:
    """One shared, order-dependent session."""

    smtpd = None
    session = None

    @classmethod
    def setup_class(cls):
        cls.smtpd = SmtpdServer(ProcessLineEvents(), settings={
            'max_processings': 1,
            'auth_mode': 'AUTH_OPTIONAL',
            'encrypt_mode': 'TLS_OPTIONAL',
            'tls_cert_cn': 'localhost',
            'pipelining_extension': False,
            'internationalization_extensions': True,
        })
        session = SmtpdSession()
        cls.smtpd.process_reset_session(session, connection_initialize=True)
        server = session.ctx.server
        server.local_host = 'localhost.local'
        server.local_ip = '127.0.0.1'
        server.local_port = '2525'
        server.local_response = 'Process line test - Welcome'
        server.remote_host = 'localhost'
        server.remote_ip = '127.0.0.1'
        server.remote_port = '65534'
        server.helo_response = 'Process line test - Greeting'
        cls.session = session

    def _line(self, line, brk=b'\r\n'):
        return run_line(self.smtpd, self.session, line, brk)

    def test_10_ehlo(self):
        result = self._line('EHLO Process line unit test')
        assert result == (
            '250-Process line test - Greeting\r\n'
            '250-8BITMIME\r\n250-SMTPUTF8\r\n'
            '250-AUTH LOGIN PLAIN\r\n250-STARTTLS\r\n250 OK'
        )
        assert self.session.ctx.server.helo == 'Process line unit test'

    def test_11_ehlo_bad_sequence(self):
        with pytest.raises(Smtpd503Exception):
            self._line('EHLO Process line unit test')

    def test_20_auth_login_simulate_fail(self):
        result = self._line('AUTH LOGIN')
        assert result == '334 ' + base64.b64encode(b'Username:').decode()
        result = self._line(base64.b64encode(b'administrator').decode())
        assert result == '334 ' + base64.b64encode(b'Password:').decode()
        with pytest.raises(Smtpd535Exception):
            self._line(base64.b64encode(b'error_password').decode())
        events = self.smtpd.events_handler
        assert events.ev_auth_authentication_id == 'administrator'
        assert events.ev_auth_authentication == 'error_password'
        server = self.session.ctx.server
        assert server.authorization_id == ''
        assert server.authentication_id == ''
        assert str(server.authenticated) == ''

    def test_21_auth_plain_authenticate_supervisor(self):
        result = self._line('AUTH PLAIN')
        assert result == '334 '
        result = self._line('AGFkbWluaXN0cmF0b3IAcGFzc3dvcmQ')
        assert result == '235 OK'
        events = self.smtpd.events_handler
        assert events.ev_auth_authentication_id == 'administrator'
        assert events.ev_auth_authentication == 'password'
        server = self.session.ctx.server
        assert server.authorization_id == 'supervisor'
        assert server.authentication_id == 'administrator'
        assert str(server.authenticated) != ''

    def test_30_mail_from(self):
        result = self._line('MAIL FROM: demo@local.local')
        assert result == '250 OK'
        assert self.session.ctx.envelope.mail_from == 'demo@local.local'

    def test_40_rcpt_to(self):
        assert self._line('RCPT TO: demo1@local.local') == '250 OK'
        assert self._line('RCPT TO: demo2@local.local') == '250 OK'
        rcpts = self.session.ctx.envelope.rcpt_tos
        assert len(rcpts) == 2
        assert rcpts[0] == 'demo1@local.local'
        assert rcpts[1] == 'demo2@local.local'

    def test_50_data(self):
        assert self._line('DATA').startswith('354 ')
        self._line('From: <demo@local.local>')
        self._line('To: <demo1@local.local>, <demo2@local.local>')
        self._line('Subject: Unit Test')
        self._line('X-test: 1')
        self._line('')
        self._line('Welcome to message!')
        self._line('Have fun.')
        self._line('..')
        result = self._line('.')
        assert result.startswith('250 ')
        assert self.session.cmd_sequence is CMD_RSET
        assert self.session.ctx.message.bytesize == -1
        assert bytes(self.session.ctx.message.data) == b''
        events = self.smtpd.events_handler
        assert events.ev_message_bytesize == 174
        assert events.ev_message_data.startswith(b'Received: test header\r\n')
        message = email.message_from_bytes(events.ev_message_data)
        assert 'demo@local.local' in message['From']
        assert message['Subject'] == 'Unit Test'
        assert message['Received'] == 'test header'
        assert int(message['X-test']) == 1
        assert message['X-inject'] == 'Y'

    def test_90_noop(self):
        assert self._line('NOOP') == '250 OK'

    def test_91_rset(self):
        assert self._line('RSET') == '250 OK'
        assert self.session.cmd_sequence is CMD_RSET

    def test_99_quit(self):
        assert self._line('QUIT') == ''
        assert self.session.cmd_sequence is CMD_QUIT
