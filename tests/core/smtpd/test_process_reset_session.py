"""
Ported from the reference suite's test/unit/process_reset_session_test.rb.

Checks the session/ctx content after ```process_reset_session``` (the
default values for every field), for both connection_initialize True and False.
"""

from tokeo.core.smtpd.server import SmtpdServer, CMD_HELO, CMD_RSET, SmtpdSession
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents


def _smtpd():
    return SmtpdServer(CaptureSmtpdEvents(), settings={})


def test_reset_helo():
    session = SmtpdSession()
    _smtpd().process_reset_session(session, connection_initialize=True)
    assert session.cmd_sequence is CMD_HELO


def test_reset_rset():
    session = SmtpdSession()
    _smtpd().process_reset_session(session, connection_initialize=False)
    assert session.cmd_sequence is CMD_RSET


def test_session_status():
    session = SmtpdSession()
    _smtpd().process_reset_session(session, connection_initialize=True)
    assert isinstance(session.auth_challenge, dict)
    server = session.ctx.server
    assert server.local_host == ''
    assert server.local_ip == ''
    assert server.local_port == ''
    assert server.local_response == ''
    assert server.remote_host == ''
    assert server.remote_ip == ''
    assert server.remote_port == ''
    assert server.proxy is None
    assert server.helo == ''
    assert server.helo_response == ''
    assert server.connected == ''
    assert server.exceptions == 0
    assert len(server.errors) == 0
    assert server.authorization_id == ''
    assert server.authentication_id == ''
    assert server.authenticated == ''
    assert server.encrypted == ''
    envelope = session.ctx.envelope
    assert envelope.mail_from == ''
    assert len(envelope.rcpt_tos) == 0
    assert envelope.encoding_body == ''
    assert envelope.encoding_utf8 == ''
    message = session.ctx.message
    assert message.received == -1
    assert message.delivered == -1
    assert message.bytesize == -1
    assert message.headers == ''
    assert message.crlf == b'\r\n'
    assert bytes(message.data) == b''
