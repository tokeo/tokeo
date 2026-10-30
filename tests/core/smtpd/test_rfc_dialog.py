"""
Tests for the RFC rules of the smtpd dialog.

Every test runs a real SMTP conversation against a live server and checks one
rule of RFC 5321, 3207 or 4954 that is not part of the full delivery tests:
RSET at any time, a later EHLO, the address literal of the server, VRFY, EXPN
and HELP with and without a handler, the 538 for AUTH without TLS, STARTTLS
within a transaction, the trace header written by the handler and the reply
of a handler fault in AUTH LOGIN.
"""

import asyncio
import base64
import email.utils
import socket

import pytest

from tokeo.core.smtpd.exc import Smtpd550Exception
from tokeo.core.smtpd.server import SmtpdServer
from tests.core.smtpd.lib.smtpd_tests_helper import (
    RecordingEvents,
    AuthEvents,
    dialog,
    auth_plain,
    read_reply,
    send_command,
    start_server,
    stop_server,
    insecure_tls_context,
    TLS_SETTINGS,
)

# ---- RSET and a later EHLO ---------------------------------------------------


def test_rset_before_helo_answers_250_and_still_needs_the_greeting():
    """RSET before HELO answers 250, MAIL then still needs EHLO (503)."""
    _, r = dialog(RecordingEvents(), {}, [b'RSET\r\n', b'MAIL FROM:<a@x>\r\n', b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n'])
    assert r[0] == ['250 OK']
    assert r[1][0].startswith('503')
    assert r[2][-1].startswith('250 ')
    assert r[3] == ['250 OK']


def test_helo_without_tls_on_tls_required_answers_530():
    """With TLS_REQUIRED a plaintext HELO answers 530, EHLO offers STARTTLS."""
    _, r = dialog(RecordingEvents(), {**TLS_SETTINGS, 'encrypt_mode': 'TLS_REQUIRED'}, [b'HELO me\r\n', b'EHLO me\r\n'])
    assert r[0][0].startswith('530')
    assert r[1][-1] == '250 STARTTLS'


def test_rset_without_tls_on_tls_required_answers_530():
    """With TLS_REQUIRED a RSET on the plaintext channel answers 530."""
    _, r = dialog(RecordingEvents(), {**TLS_SETTINGS, 'encrypt_mode': 'TLS_REQUIRED'}, [b'EHLO me\r\n', b'RSET\r\n'])
    assert r[1][0].startswith('530')


def test_a_later_ehlo_resets_the_transaction():
    """A second EHLO drops the open transaction, RCPT then needs MAIL again."""
    commands = [
        b'EHLO me\r\n',
        b'MAIL FROM:<a@x>\r\n',
        b'RCPT TO:<b@y>\r\n',
        b'EHLO again\r\n',
        b'RCPT TO:<b@y>\r\n',
        b'MAIL FROM:<a@x>\r\n',
    ]
    _, r = dialog(RecordingEvents(), {}, commands)
    assert r[3][-1].startswith('250 ')
    assert r[4][0].startswith('503')
    assert r[5] == ['250 OK']


def test_a_later_ehlo_keeps_the_auth():
    """A second EHLO keeps a successful AUTH, a further AUTH answers 503."""
    plain = b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n'
    _, r = dialog(AuthEvents(), {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n', plain, b'EHLO again\r\n', plain])
    assert r[1] == ['235 OK']
    assert r[3][0].startswith('503')


# ---- the name of the server --------------------------------------------------


def test_without_a_host_name_the_server_names_itself_by_an_address_literal():
    """Without reverse lookup greeting, HELO and EHLO name [127.0.0.1]."""
    greeting, r = dialog(RecordingEvents(), {'do_dns_reverse_lookup': False}, [b'HELO me\r\n', b'RSET\r\n', b'EHLO me\r\n'])
    assert greeting == ['220 [127.0.0.1] says welcome!']
    assert r[0] == ['250 [127.0.0.1] at your service!']
    assert r[2] == ['250 [127.0.0.1] at your service!']


def test_an_ipv6_address_literal_is_tagged():
    """On IPv6 the address literal carries the IPv6 tag."""
    if not socket.has_ipv6:
        pytest.skip('no IPv6 on this host')

    async def go():
        srv = SmtpdServer(RecordingEvents(), settings={'do_dns_reverse_lookup': False})
        try:
            server = await asyncio.start_server(srv._on_connection, '::1', 0)
        except OSError:
            pytest.skip('no IPv6 loopback on this host')
        port = server.sockets[0].getsockname()[1]
        task = asyncio.create_task(server.serve_forever())
        try:
            reader, writer = await asyncio.open_connection('::1', port)
            greeting = await read_reply(reader)
            writer.close()
            return greeting
        finally:
            await stop_server(server, task)

    assert asyncio.run(go()) == ['220 [IPv6:::1] says welcome!']


# ---- VRFY, EXPN and HELP -----------------------------------------------------


def test_vrfy_expn_and_help_without_a_handler():
    """Without a handler VRFY answers 252, EXPN and HELP 502, also before HELO."""
    _, r = dialog(RecordingEvents(), {}, [b'VRFY postmaster\r\n', b'EXPN list\r\n', b'HELP\r\n'])
    assert r[0] == ['252 Cannot VRFY user, but will accept message and attempt delivery']
    assert r[1] == ['502 Command not implemented']
    assert r[2] == ['502 Command not implemented']


class ReplyEvents(RecordingEvents):
    """Answers VRFY, EXPN of the team and HELP, leaves other EXPN to the server."""

    def __init__(self, options=None):
        super().__init__(options)
        self.args = []

    def on_vrfy_event(self, ctx, vrfy_data):
        self.args.append(vrfy_data)
        return f'250 <{vrfy_data}@example.com>'

    def on_expn_event(self, ctx, expn_data):
        self.args.append(expn_data)
        return '250 <team@example.com>' if expn_data == 'team' else None

    def on_help_event(self, ctx, help_data):
        self.args.append(help_data)
        return '214 See the documentation'


def test_vrfy_and_expn_without_an_argument_answer_501():
    """VRFY and EXPN need a string to look up, HELP takes none."""
    ev = ReplyEvents()
    _, r = dialog(ev, {}, [b'VRFY\r\n', b'EXPN \r\n', b'HELP\r\n'])
    assert r[0][0].startswith('501')
    assert r[1][0].startswith('501')
    assert r[2] == ['214 See the documentation']
    assert ev.args == ['']


def test_vrfy_expn_and_help_take_the_reply_of_the_handler():
    """A reply line of the handler answers, None keeps the answer of the server."""
    ev = ReplyEvents()
    commands = [b'EHLO me\r\n', b'VRFY postmaster\r\n', b'EXPN team\r\n', b'EXPN list\r\n', b'HELP\r\n']
    _, r = dialog(ev, {}, commands)
    assert r[1] == ['250 <postmaster@example.com>']
    assert r[2] == ['250 <team@example.com>']
    assert r[3] == ['502 Command not implemented']
    assert r[4] == ['214 See the documentation']
    assert ev.args == ['postmaster', 'team', 'list', '']


class FaultyReplyEvents(RecordingEvents):
    """Answers by the argument: a wrong result, an error or a reject."""

    def _answer(self, data):
        if data == 'result':
            return 42
        if data == 'error':
            raise ValueError('broken')
        raise Smtpd550Exception

    def on_vrfy_event(self, ctx, vrfy_data):
        return self._answer(vrfy_data)

    def on_expn_event(self, ctx, expn_data):
        return self._answer(expn_data)

    def on_help_event(self, ctx, help_data):
        return self._answer(help_data)


@pytest.mark.parametrize('verb', [b'VRFY', b'EXPN', b'HELP'])
def test_vrfy_expn_and_help_answer_a_fault_of_the_handler(verb):
    """Another result or an exception answers 451, an SmtpdException its code."""
    commands = [b'EHLO me\r\n'] + [verb + b' ' + data + b'\r\n' for data in (b'result', b'error', b'reject')]
    _, r = dialog(FaultyReplyEvents(), {}, commands)
    assert [reply[0][:3] for reply in r[1:]] == ['451', '451', '550']


def test_vrfy_expn_and_help_without_tls_on_tls_required_answer_530():
    """With TLS_REQUIRED VRFY, EXPN and HELP on the plaintext channel answer 530."""
    commands = [b'EHLO me\r\n', b'VRFY x\r\n', b'EXPN x\r\n', b'HELP\r\n']
    _, r = dialog(RecordingEvents(), {**TLS_SETTINGS, 'encrypt_mode': 'TLS_REQUIRED'}, commands)
    assert [reply[0][:3] for reply in r[1:]] == ['530', '530', '530']


# ---- STARTTLS and AUTH -------------------------------------------------------


def test_starttls_with_an_argument_answers_501():
    """STARTTLS takes no argument, one answers 501."""
    _, r = dialog(RecordingEvents(), TLS_SETTINGS, [b'EHLO me\r\n', b'STARTTLS now\r\n'])
    assert r[1][0].startswith('501')


def test_starttls_within_a_transaction_drops_it():
    """STARTTLS after MAIL drops the transaction, RCPT then answers 503."""

    async def go():
        server, port, task = await start_server(RecordingEvents(), TLS_SETTINGS)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            await send_command(reader, writer, b'EHLO me\r\n')
            await send_command(reader, writer, b'MAIL FROM:<a@x>\r\n')
            await send_command(reader, writer, b'STARTTLS\r\n')
            await writer.start_tls(insecure_tls_context())
            await send_command(reader, writer, b'EHLO secure\r\n')
            rcpt = await send_command(reader, writer, b'RCPT TO:<b@y>\r\n')
            writer.close()
            return rcpt
        finally:
            await stop_server(server, task)

    assert asyncio.run(go())[0].startswith('503')


def test_auth_login_with_a_not_stripped_authzid_answers_454():
    """AUTH LOGIN with a not stripped authorization id answers 454."""
    ev = AuthEvents()
    ev.authz_return = ' user'
    user, password = base64.b64encode(b'user') + b'\r\n', base64.b64encode(b'secret') + b'\r\n'
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n', b'AUTH LOGIN\r\n', user, password])
    assert r[3][0].startswith('454')


# ---- the trace header --------------------------------------------------------


class TraceEvents(RecordingEvents):
    """Writes the Received header at the start of DATA (RFC 5321 4.4)."""

    def __init__(self, options=None):
        super().__init__(options)
        self.spooled = []

    def on_message_data_start_event(self, ctx):
        server = ctx.server
        ctx.message.data += (
            f'Received: from {server.helo} ({server.remote_host} [{server.remote_ip}])\r\n'
            f'\tby {server.local_host} with ESMTP id {ctx.id};\r\n'
            f'\t{email.utils.format_datetime(ctx.message.received)}\r\n'
        ).encode()

    def on_message_data_event(self, ctx):
        super().on_message_data_event(ctx)
        if ctx.message.spooler:
            self.spooled.append(open(ctx.message.spooler.path, 'rb').read())


_TRACE_COMMANDS = [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n', b'Subject: hi\r\n\r\nHello\r\n.\r\n']


def test_the_handler_writes_the_trace_header_at_the_start():
    """A Received header written on the start of DATA heads the message."""
    ev = TraceEvents()
    _, r = dialog(ev, {}, _TRACE_COMMANDS)
    assert r[4][0].startswith('250')
    data = ev.messages[0][0]
    assert data.startswith(b'Received: from me (')
    assert data.endswith(b'\r\nSubject: hi\r\n\r\nHello')


def test_the_trace_header_reaches_the_spool_file(tmp):
    """With spooling the Received header heads the spool file too."""
    ev = TraceEvents()
    _, r = dialog(ev, {'spool': f'{tmp.dir}/trace-'}, _TRACE_COMMANDS)
    assert r[4][0].startswith('250')
    assert ev.spooled[0].startswith(b'Received: from me (')
    assert ev.spooled[0].endswith(b'\r\nSubject: hi\r\n\r\nHello')
