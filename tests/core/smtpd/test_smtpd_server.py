"""
Tests for tokeo.core.smtpd.server (the own asyncio SMTP server).

Everything is exercised over a real SMTP conversation against a live server:
the full dialog and reference-faithful behaviour (null sender, de-dot-stuffing,
byte-exact body, multiple transactions, sequence enforcement, handler rejects),
the security enforcement we own (PIPELINING rejection, CRLF strictness, data_size,
buffer overrun, command timeout), internationalization, the connection/processing
limits, the PROXY protocol, the graceful shutdown, AUTH PLAIN/LOGIN and STARTTLS.
"""

import base64
import asyncio
import threading

import pytest

from tokeo.core.smtpd.server import SmtpdServer
from tokeo.core.smtpd.events import threaded
from tokeo.core.smtpd.exc import Smtpd550Exception, Smtpd450Exception, Smtpd421Exception, Smtpd555Exception
from tests.utils import wait_until_async
from tests.core.smtpd.lib.smtpd_tests_helper import (
    RecordingEvents,
    AuthEvents,
    dialog,
    auth_plain,
    read_reply,
    send_command,
    start_server,
    stop_server,
    start_smtpd,
    insecure_tls_context,
    TLS_SETTINGS,
)

# --- full delivery and events ------------------------------------------------


def test_full_delivery_and_events():
    """A full dialog delivers the body byte-exact and fires every event."""
    ev = RecordingEvents()
    greeting, r = dialog(
        ev,
        {},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<a@x>\r\n',
            b'RCPT TO:<b@y>\r\n',
            b'DATA\r\n',
            b'Subject: hi\r\n\r\nHello world\r\n.\r\n',
            b'QUIT\r\n',
        ],
    )
    assert greeting[0].startswith('220')
    assert r[0][0].startswith('250-') and r[0][-1] == '250 OK'
    assert r[1] == ['250 OK'] and r[2] == ['250 OK']
    assert r[3][0].startswith('354')
    assert r[4][0].startswith('250')
    assert r[5][0].startswith('221')
    assert 'connect' in ev.fired and 'disconnect' in ev.fired
    assert ev.fired.count('start') == 1 and 'headers' in ev.fired and 'recv' in ev.fired
    data, mail_from, rcpts = ev.messages[0]
    assert data == b'Subject: hi\r\n\r\nHello world'
    assert mail_from == '<a@x>' and rcpts == ['<b@y>']


def test_helo_gets_single_line_250():
    """HELO answers one line of 250."""
    _, r = dialog(RecordingEvents(), {}, [b'HELO me\r\n'])
    assert r[0][0].startswith('250 ') and len(r[0]) == 1


def test_ehlo_advertises_extensions_when_enabled():
    """EHLO lists 8BITMIME, SMTPUTF8 and PIPELINING when enabled."""
    settings = {'internationalization_extensions': True, 'pipelining_extension': True}
    _, r = dialog(RecordingEvents(), settings, [b'EHLO me\r\n'])
    text = '\n'.join(r[0])
    assert '8BITMIME' in text and 'SMTPUTF8' in text and 'PIPELINING' in text
    assert r[0][-1] == '250 OK'


# --- reference-faithful behaviour --------------------------------------------


def test_null_sender_accepted():
    """MAIL FROM:<> answers "250 OK"."""
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'MAIL FROM:<>\r\n'])
    assert r[1] == ['250 OK']


def test_de_dot_stuffing_and_byte_exact_body():
    """One leading dot per line is removed, the final CRLF chomped."""
    ev = RecordingEvents()
    dialog(
        ev,
        {},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<a@x>\r\n',
            b'RCPT TO:<b@y>\r\n',
            b'DATA\r\n',
            b'..dotted\r\n.leading\r\nplain\r\n.\r\n',
        ],
    )
    # a leading dot is stripped from each line; the final CRLF is chomped
    assert ev.messages[0][0] == b'.dotted\r\nleading\r\nplain'


def test_multiple_transactions_with_rset():
    """After RSET only the second transaction is delivered."""
    ev = RecordingEvents()
    _, r = dialog(
        ev,
        {},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<a@x>\r\n',
            b'RSET\r\n',
            b'MAIL FROM:<c@z>\r\n',
            b'RCPT TO:<d@z>\r\n',
            b'DATA\r\n',
            b'body\r\n.\r\n',
        ],
    )
    assert r[2] == ['250 OK']  # RSET
    assert r[6][0].startswith('250')
    # after RSET the second transaction is the only delivered message
    assert ev.messages[0][1] == '<c@z>' and ev.messages[0][2] == ['<d@z>']


def test_mail_from_rewrite_by_handler():
    """A sender returned by on_mail_from_event replaces the given one."""
    ev = RecordingEvents()
    ev.rewrite_from = '<rewritten@x>'
    ev.messages.clear()
    dialog(
        ev,
        {},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<a@x>\r\n',
            b'RCPT TO:<b@y>\r\n',
            b'DATA\r\n',
            b'x\r\n.\r\n',
        ],
    )
    assert ev.messages[0][1] == '<rewritten@x>'


def test_noop_and_unknown_command():
    """NOOP answers "250 OK", an unknown command 500."""
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'NOOP\r\n', b'FOOBAR\r\n'])
    assert r[1] == ['250 OK']
    assert r[2][0].startswith('500')


# --- sequence and reject codes ---------------------------------


def test_sequence_rcpt_before_mail_is_503():
    """RCPT before MAIL answers 503."""
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'RCPT TO:<x>\r\n'])
    assert r[1][0].startswith('503')


def test_sequence_data_before_rcpt_is_503():
    """DATA before RCPT answers 503."""
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'DATA\r\n'])
    assert r[2][0].startswith('503')


def test_mail_before_helo_is_503():
    """MAIL before HELO answers 503."""
    _, r = dialog(RecordingEvents(), {}, [b'MAIL FROM:<a@x>\r\n'])
    assert r[0][0].startswith('503')


def test_handler_reject_maps_to_550():
    """Smtpd550Exception of a handler answers 550."""
    ev = RecordingEvents()
    ev.reject_rcpt = {'deny'}
    _, r = dialog(ev, {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<deny@x>\r\n'])
    assert r[2][0].startswith('550')


def test_handler_reject_transient_450():
    """Smtpd450Exception of a handler answers 450."""

    class E(RecordingEvents):

        def on_rcpt_to_event(self, ctx, rcpt_to):
            raise Smtpd450Exception

    _, r = dialog(E(), {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n'])
    assert r[2][0].startswith('450')


def test_handler_reject_parameter_555():
    """Smtpd555Exception of a handler answers 555 for a parameter it refuses."""

    class E(RecordingEvents):

        def on_mail_from_event(self, ctx, mail_from):
            if ctx.envelope.encoding_utf8:
                raise Smtpd555Exception

    settings = {'internationalization_extensions': True}
    _, r = dialog(E(), settings, [b'EHLO me\r\n', b'MAIL FROM:<a@x> SMTPUTF8\r\n'])
    assert r[1] == ['555 MAIL FROM/RCPT TO parameters not recognized or not implemented']


@pytest.mark.parametrize('event', ['mail', 'rcpt'])
def test_handler_bug_contained_as_451(event):
    """Any other exception of a MAIL/RCPT handler answers 451, the dialog lasts."""

    class E(RecordingEvents):

        def on_mail_from_event(self, ctx, mail_from):
            if event == 'mail':
                raise RuntimeError('boom')
            return True

        def on_rcpt_to_event(self, ctx, rcpt_to):
            raise RuntimeError('boom')

    _, r = dialog(E(), {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'NOOP\r\n'])
    assert r[1 if event == 'mail' else 2][0].startswith('451')
    assert r[3] == ['250 OK']


@pytest.mark.parametrize(
    'result, reply, address',
    [
        # None or False refuses
        (None, '550', None),
        (False, '550', None),
        # True keeps the address as sent
        (True, '250', '<a@x>'),
        # a stripped string replaces it, also the empty one
        ('<b@x>', '250', '<b@x>'),
        ('', '250', ''),
        # anything else is a fault of the handler
        (' <b@x>', '451', None),
        ('<b@x> ', '451', None),
        (1, '451', None),
        (b'<b@x>', '451', None),
    ],
)
@pytest.mark.parametrize('event', ['mail', 'rcpt'])
def test_mail_rcpt_event_results(event, result, reply, address):
    """The result of a MAIL/RCPT handler refuses, keeps or replaces the address."""

    class E(RecordingEvents):

        def __init__(self, options=None):
            super().__init__(options)
            self.seen = []

        def on_mail_from_event(self, ctx, mail_from):
            return result if event == 'mail' else True

        def on_rcpt_to_event(self, ctx, rcpt_to):
            # a probe recipient shows what the server stored before
            self.seen.append((ctx.envelope.mail_from, list(ctx.envelope.rcpt_tos)))
            return True if rcpt_to == '<probe@y>' else result

    if event == 'mail':
        commands = [b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<probe@y>\r\n']
    else:
        commands = [b'MAIL FROM:<m@x>\r\n', b'RCPT TO:<a@x>\r\n', b'RCPT TO:<probe@y>\r\n']
    ev = E()
    _, r = dialog(ev, {}, [b'EHLO me\r\n', *commands])
    assert r[-2][0].startswith(reply)
    if event == 'mail':
        # a refused MAIL leaves no transaction for the probe
        assert ev.seen == ([(address, [])] if address is not None else [])
    else:
        assert ev.seen[-1] == ('<m@x>', [address] if address is not None else [])


# --- security we own ---------------------------------------------------------


def test_pipelining_rejected_when_disabled():
    """Two commands in one segment answer 500 without PIPELINING."""

    async def go():
        server, port, task = await start_server(RecordingEvents(), {})
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            await send_command(reader, writer, b'EHLO me\r\n')
            # two commands in one segment -> the first sees a buffered next line
            writer.write(b'NOOP\r\nNOOP\r\n')
            await writer.drain()
            first = await read_reply(reader)
            writer.close()
            return first
        finally:
            await stop_server(server, task)

    first = asyncio.run(go())
    assert first[0].startswith('500')


def test_pipelining_allowed_when_enabled():
    """With PIPELINING two commands in one segment both answer 250."""

    async def go():
        server, port, task = await start_server(RecordingEvents(), {'pipelining_extension': True})
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            await send_command(reader, writer, b'EHLO me\r\n')
            writer.write(b'NOOP\r\nNOOP\r\n')
            await writer.drain()
            a = await read_reply(reader)
            b = await read_reply(reader)
            writer.close()
            return a, b
        finally:
            await stop_server(server, task)

    a, b = asyncio.run(go())
    assert a == ['250 OK'] and b == ['250 OK']


def test_crlf_strict_rejects_bare_lf():
    """CRLF_STRICT answers a command with bare LF by 500."""
    _, r = dialog(RecordingEvents(), {'crlf_mode': 'CRLF_STRICT'}, [b'EHLO me\n'])
    assert r[0][0].startswith('500')


def test_data_size_limit_552():
    """A body over data_size answers 552."""
    ev = RecordingEvents()
    _, r = dialog(
        ev,
        {'data_size': 50},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<a@x>\r\n',
            b'RCPT TO:<b@y>\r\n',
            b'DATA\r\n',
            b'X' * 200 + b'\r\n',
        ],
    )
    assert r[4][0].startswith('552')


def test_buffer_overrun_421():
    """A line over io_buffer_max_size answers 421 and drops the connection."""
    _, r = dialog(RecordingEvents(), {'io_buffer_max_size': 100}, [b'EHLO ' + b'a' * 300 + b'\r\n'])
    assert r[0][0].startswith('421')


def test_command_timeout_closes_connection():
    """A silent client gets 421 after io_cmd_timeout."""

    async def go():
        server, port, task = await start_server(RecordingEvents(), {'io_cmd_timeout': 1})
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            # send nothing; the server must time out and answer 421
            reply = await asyncio.wait_for(read_reply(reader), timeout=3)
            writer.close()
            return reply
        finally:
            await stop_server(server, task)

    reply = asyncio.run(go())
    assert reply[0].startswith('421')


def test_read_line_takes_a_buffered_line_without_waiting(monkeypatch):
    """A complete line in the buffer is read without asyncio.wait."""
    from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents

    async def go():
        srv = SmtpdServer(CaptureSmtpdEvents(), settings={'io_cmd_timeout': 30})
        reader = asyncio.StreamReader()
        reader.feed_data(b'HELO x\r\nMAIL FROM:<a@b>\r\n')

        def boom(*args, **kwargs):
            raise AssertionError('asyncio.wait must not be used when a line is buffered')

        monkeypatch.setattr(asyncio, 'wait', boom)
        assert await srv._read_line(reader, srv.io_cmd_timeout) == b'HELO x\r\n'
        assert await srv._read_line(reader, srv.io_cmd_timeout) == b'MAIL FROM:<a@b>\r\n'

    asyncio.run(go())


def test_read_line_times_out_on_partial_buffered_line():
    """A partial line in the buffer still times out."""
    from tokeo.core.smtpd.exc import SmtpdIOTimeoutException
    from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents

    async def go():
        srv = SmtpdServer(CaptureSmtpdEvents())
        reader = asyncio.StreamReader()
        reader.feed_data(b'incomplete without newline')
        # a short timeout of the read itself, the config allows 1 second at least
        with pytest.raises(SmtpdIOTimeoutException):
            await srv._read_line(reader, 0.1)

    asyncio.run(go())


# --- internationalization ----------------------------------------------------


def test_body_and_smtputf8_params_stripped():
    """BODY and SMTPUTF8 are stripped, the handler gets the address only."""
    ev = RecordingEvents()
    dialog(
        ev,
        {'internationalization_extensions': True},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<a@x> BODY=8BITMIME SMTPUTF8\r\n',
            b'RCPT TO:<b@y>\r\n',
            b'DATA\r\n',
            b'x\r\n.\r\n',
        ],
    )
    # the address reaches the handler without the params
    assert ev.messages[0][1] == '<a@x>'


def test_body_param_rejected_without_intl():
    """BODY is not announced without internationalization_extensions: 555."""
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x> BODY=8BITMIME\r\n'])
    assert r[1][0].startswith('555')


@pytest.mark.parametrize(
    'settings, command, reply',
    [
        # unknown or not announced: 555
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> SIZE=1000', '555'),
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> PARAM="a', '555'),
        ({}, b'MAIL FROM:<a@x> SMTPUTF8', '555'),
        ({}, b'MAIL FROM:<a@x> AUTH=<>', '555'),
        ({**TLS_SETTINGS, 'encrypt_mode': 'TLS_WHEN_AUTH', 'auth_mode': 'AUTH_OPTIONAL'}, b'MAIL FROM:<a@x> AUTH=<>', '555'),
        ({}, b'RCPT TO:<b@y> NOTIFY=NEVER', '555'),
        # malformed, wrong value or twice: 501
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> BODY=FOO', '501'),
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> BODY', '501'),
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> SMTPUTF8=1', '501'),
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> BODY=7BIT body=8bitmime', '501'),
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> SIZE=', '501'),
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> SIZE=1=2', '501'),
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> -SIZE=1', '501'),
        ({'internationalization_extensions': True}, b'MAIL FROM:<a@x> Leer"', '501'),
        ({'auth_mode': 'AUTH_OPTIONAL'}, b'MAIL FROM:<a@x> AUTH', '501'),
        ({'auth_mode': 'AUTH_OPTIONAL'}, b'MAIL FROM:<a@x> AUTH=a+Zb@x', '501'),
        ({'auth_mode': 'AUTH_OPTIONAL'}, b'MAIL FROM:<a@x> AUTH=a=b@x', '501'),
        ({}, b'RCPT TO:<b@y> Leer"', '555'),
    ],
)
def test_mail_rcpt_parameters_are_checked_strictly(settings, command, reply):
    """Only announced parameters in valid form are accepted at MAIL and RCPT."""
    ev = RecordingEvents()
    commands = [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n'] if command.startswith(b'RCPT') else [b'EHLO me\r\n']
    _, r = dialog(ev, settings, commands + [command + b'\r\n'])
    assert r[-1][0].startswith(reply)
    # a refused MAIL does not reach the handler
    assert ev.fired.count('mail') == (1 if command.startswith(b'RCPT') else 0)


@pytest.mark.parametrize(
    'command, address',
    [
        (b'MAIL FROM:<a@x>', '<a@x>'),
        (b'MAIL FROM: <a@x>', '<a@x>'),
        (b'MAIL FROM:a@x', 'a@x'),
        (b'MAIL FROM:<>', '<>'),
        (b'MAIL FROM:', ''),
        (b'MAIL FROM:<a@x>  body=8bitmime   smtputf8', '<a@x>'),
    ],
)
def test_mail_from_hands_the_address_as_sent(command, address):
    """The handler gets the address as sent, without the parameters."""

    class E(RecordingEvents):

        def on_mail_from_event(self, ctx, mail_from):
            self.mail = (mail_from, ctx.envelope.encoding_body, ctx.envelope.encoding_utf8)
            return True

    ev = E()
    _, r = dialog(ev, {'internationalization_extensions': True}, [b'EHLO me\r\n', command + b'\r\n'])
    assert r[1] == ['250 OK']
    params = b'8bitmime' in command
    assert ev.mail == (address, '8bitmime' if params else '', 'utf8' if params else '')


def test_rejected_mail_leaves_no_envelope_behind():
    """BODY and SMTPUTF8 of a rejected MAIL do not reach the next transaction."""

    class E(RecordingEvents):

        def __init__(self, options=None):
            super().__init__(options)
            self.encodings = []

        def on_mail_from_event(self, ctx, mail_from):
            self.encodings.append((mail_from, ctx.envelope.encoding_body, ctx.envelope.encoding_utf8))
            if 'bad' in mail_from:
                raise Smtpd550Exception
            return True

        def on_message_data_event(self, ctx):
            self.encodings.append(('data', ctx.envelope.encoding_body, ctx.envelope.encoding_utf8))

    ev = E()
    _, r = dialog(
        ev,
        {'internationalization_extensions': True},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<bad@x> BODY=7BIT SMTPUTF8\r\n',
            b'MAIL FROM:<good@x>\r\n',
            b'RCPT TO:<b@y>\r\n',
            b'DATA\r\n',
            b'x\r\n.\r\n',
        ],
    )
    assert r[1][0].startswith('550')
    assert ev.encodings == [('<bad@x>', '7bit', 'utf8'), ('<good@x>', '', ''), ('data', '', '')]


# --- limits ------------------------------------------------------------------


def test_max_connections_refuses_with_421():
    """A connection over max_connections gets 421 instead of 220."""

    async def go():
        server, port, task = await start_server(RecordingEvents(), {'max_connections': 1, 'max_processings': 1})
        try:
            r1, w1 = await asyncio.open_connection('127.0.0.1', port)
            b1 = await read_reply(r1)
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            b2 = await read_reply(r2)
            w1.close()
            w2.close()
            return b1, b2
        finally:
            await stop_server(server, task)

    b1, b2 = asyncio.run(go())
    assert b1[0].startswith('220')
    assert b2[0].startswith('421')


def test_threaded_event_runs_off_loop():
    """A @threaded data event runs in another thread than the loop."""

    class E(RecordingEvents):

        def on_connect_event(self, ctx):
            # a plain event runs on the thread of the loop
            self.loop_thread = threading.get_ident()

        @threaded
        def on_message_data_event(self, ctx):
            self.data_thread = threading.get_ident()
            self.messages.append((bytes(ctx.message.data), ctx.envelope.mail_from, list(ctx.envelope.rcpt_tos)))

    ev = E()
    _, r = dialog(
        ev,
        {},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<a@x>\r\n',
            b'RCPT TO:<b@y>\r\n',
            b'DATA\r\n',
            b'hi\r\n.\r\n',
        ],
    )
    assert r[4][0].startswith('250')
    assert ev.messages and ev.messages[0][0] == b'hi'
    assert ev.data_thread != ev.loop_thread


@pytest.mark.parametrize('mode', ['CRLF_ENSURE', 'CRLF_LEAVE', 'CRLF_STRICT'])
def test_delivery_works_in_all_crlf_modes(mode):
    """A plain message delivers in every crlf_mode."""
    ev = RecordingEvents()
    _, r = dialog(
        ev,
        {'crlf_mode': mode},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<a@x>\r\n',
            b'RCPT TO:<b@y>\r\n',
            b'DATA\r\n',
            b'body\r\n.\r\n',
        ],
    )
    assert r[4][0].startswith('250')
    assert ev.messages[0][0].rstrip(b'\r\n') == b'body'


def test_on_connect_fires_even_when_refused():
    """on_connect_event fires also for a connection refused by 421."""
    ev = RecordingEvents()

    async def go():
        server, port, task = await start_server(ev, {'max_connections': 1, 'max_processings': 1})
        try:
            r1, w1 = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(r1)
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            b2 = await read_reply(r2)
            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()
            return b2
        finally:
            await stop_server(server, task)

    b2 = asyncio.run(go())
    assert b2[0].startswith('421')
    assert ev.fired.count('connect') == 2


def test_max_processings_waits_for_free_slot():
    """With one slot a second client gets 220 only after the first quits."""

    async def go():
        server, port, task = await start_server(RecordingEvents(), {'max_processings': 1})
        try:
            r1, w1 = await asyncio.open_connection('127.0.0.1', port)
            b1 = await read_reply(r1)
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            blocked = False
            try:
                await asyncio.wait_for(read_reply(r2), timeout=0.3)
            except asyncio.TimeoutError:
                blocked = True
            # free the slot by ending the first dialog
            w1.write(b'QUIT\r\n')
            await w1.drain()
            await read_reply(r1)
            w1.close()
            b2 = await asyncio.wait_for(read_reply(r2), timeout=2)
            w2.close()
            return b1, blocked, b2
        finally:
            await stop_server(server, task)

    b1, blocked, b2 = asyncio.run(go())
    assert b1[0].startswith('220')
    assert blocked is True
    assert b2[0].startswith('220')


# --- PROXY protocol ----------------------------------------------------------


class ProxyEvents(RecordingEvents):
    """Records the proxy_data passed to on_proxy_event."""

    def __init__(self, options=None):
        super().__init__(options)
        self.proxy_seen = None
        self.proxy_rewrite = None
        self.proxy_on_ctx = None

    def on_proxy_event(self, ctx, proxy_data):
        self.proxy_seen = dict(proxy_data)
        return self.proxy_rewrite

    def on_helo_event(self, ctx, helo):
        # captured after PROXY, so ctx.server.proxy reflects what was stored
        self.proxy_on_ctx = ctx.server.proxy


async def _dialog_raw(events, settings, steps, has_server_greeting=True):
    """
    Run a dialog where each step says whether to read a reply.

    ### Args

    - **steps** (list): tuples ```(bytes_to_send, expect_reply)```; when
        expect_reply is False nothing is read
    - **has_server_greeting** (bool): read the 220 greeting on connect; False
        with proxy_extension, where the greeting answers the PROXY header

    ### Returns

    - **tuple**: ```(greeting, replies, closed)``` -- the greeting on connect
        (or None), replies for the steps that expected one, and whether the
        server closed the connection at the end

    """
    server, port, task = await start_server(events, settings)
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        greeting = await read_reply(reader) if has_server_greeting else None
        replies = []
        for chunk, expect in steps:
            writer.write(chunk)
            await writer.drain()
            if expect:
                replies.append(await read_reply(reader))
        closed = False
        try:
            tail = await asyncio.wait_for(reader.read(1), timeout=0.2)
            closed = tail == b''
        except asyncio.TimeoutError:
            closed = False
        writer.close()
        await writer.wait_closed()
        return greeting, replies, closed
    finally:
        await stop_server(server, task)


def test_proxy_disabled_is_unknown_command():
    """Without proxy_extension a PROXY line answers 500."""
    _, r = dialog(RecordingEvents(), {}, [b'PROXY TCP4 1.2.3.4 5.6.7.8 111 222\r\n'])
    assert r[0][0].startswith('500')


def test_unknown_command_counts_and_allows_abuse_abort():
    """
    Unknown commands count in ctx.server.exceptions: two answer 500, the
    third lets the handler abort by 421.
    """

    class AbuseGuard(RecordingEvents):

        def on_process_line_unknown_event(self, ctx, line):
            if ctx.server.exceptions >= 2:
                raise Smtpd421Exception

    _, r = dialog(AbuseGuard(), {}, [b'FOO one\r\n', b'BAR two\r\n', b'BAZ three\r\n'])
    assert r[0][0].startswith('500')
    assert r[1][0].startswith('500')
    assert any(line.startswith('421') for line in r[2])


def test_proxy_tcp4_fills_ctx_and_fires_event():
    """A TCP4 header fires on_proxy_event and fills ctx.server.proxy."""
    ev = ProxyEvents()

    async def go():
        # no greeting on connect, the PROXY header is answered by it
        greeting, replies, closed = await _dialog_raw(
            ev,
            {'proxy_extension': True},
            [(b'PROXY TCP4 1.2.3.4 5.6.7.8 1111 2222\r\n', True), (b'EHLO me\r\n', True)],
            has_server_greeting=False,
        )
        return replies

    replies = asyncio.run(go())
    assert replies[0][0].startswith('220')
    assert replies[1][-1] == '250 OK'
    assert ev.proxy_seen is not None
    assert ev.proxy_seen['proto'] == 'TCP4'
    assert ev.proxy_seen['source_ip'] == '1.2.3.4' and ev.proxy_seen['source_port'] == 1111
    assert ev.proxy_seen['dest_ip'] == '5.6.7.8' and ev.proxy_seen['dest_port'] == 2222
    # the parsed dict is stored on the ctx for later events
    assert ev.proxy_on_ctx['source_ip'] == '1.2.3.4' and ev.proxy_on_ctx['proto'] == 'TCP4'


def test_proxy_unknown_is_accepted():
    """PROXY UNKNOWN is accepted without addresses."""
    ev = ProxyEvents()

    async def go():
        return await _dialog_raw(
            ev,
            {'proxy_extension': True},
            [(b'PROXY UNKNOWN\r\n', True), (b'EHLO me\r\n', True)],
            has_server_greeting=False,
        )

    _, replies, _ = asyncio.run(go())
    assert replies[0][0].startswith('220')
    assert replies[1][-1] == '250 OK'
    assert ev.proxy_seen['proto'] == 'UNKNOWN' and ev.proxy_seen['source_ip'] is None


def test_proxy_event_can_rewrite():
    """The data returned by on_proxy_event is stored instead."""
    ev = ProxyEvents()
    ev.proxy_rewrite = {'proto': 'TCP4', 'source_ip': '9.9.9.9'}

    async def go():
        return await _dialog_raw(
            ev,
            {'proxy_extension': True},
            [(b'PROXY TCP4 1.2.3.4 5.6.7.8 1111 2222\r\n', True), (b'EHLO me\r\n', True)],
            has_server_greeting=False,
        )

    asyncio.run(go())
    # the event saw the parsed data, but its return value replaced what is stored
    assert ev.proxy_seen['source_ip'] == '1.2.3.4'
    assert ev.proxy_on_ctx == {'proto': 'TCP4', 'source_ip': '9.9.9.9'}


def test_proxy_not_first_aborts_421():
    """With proxy_extension a first line other than PROXY gets 421."""

    async def go():
        return await _dialog_raw(
            ProxyEvents(),
            {'proxy_extension': True},
            [(b'EHLO me\r\n', True)],
            has_server_greeting=False,
        )

    _, replies, closed = asyncio.run(go())
    assert replies[0][0].startswith('421')
    assert closed is True


def test_proxy_illegal_command_aborts_421():
    """An illegal PROXY line gets 421 and the connection closes."""

    async def go():
        return await _dialog_raw(
            ProxyEvents(),
            {'proxy_extension': True},
            [(b'PROXY GARBAGE\r\n', True)],
            has_server_greeting=False,
        )

    _, replies, closed = asyncio.run(go())
    assert replies[0][0].startswith('421')
    assert closed is True


def test_proxy_bad_params_abort_421():
    """TCP4 with IPv6 addresses gets 421 and the connection closes."""

    async def go():
        return await _dialog_raw(
            ProxyEvents(),
            {'proxy_extension': True},
            [(b'PROXY TCP4 ::1 ::2 1 2\r\n', True)],
            has_server_greeting=False,
        )

    _, replies, closed = asyncio.run(go())
    assert replies[0][0].startswith('421')
    assert closed is True


def test_proxy_twice_aborts_421():
    """A second PROXY line gets 421 and the connection closes."""

    async def go():
        return await _dialog_raw(
            ProxyEvents(),
            {'proxy_extension': True},
            [(b'PROXY TCP4 1.2.3.4 5.6.7.8 1 2\r\n', True), (b'PROXY TCP4 9.9.9.9 8.8.8.8 3 4\r\n', True)],
            has_server_greeting=False,
        )

    _, replies, closed = asyncio.run(go())
    assert replies[0][0].startswith('220')
    assert replies[1][0].startswith('421')
    assert closed is True


def test_proxy_header_timeout_aborts_421():
    """No PROXY header within io_proxy_cmd_timeout gets 421."""

    async def go():
        server, port, task = await start_server(ProxyEvents(), {'proxy_extension': True, 'io_proxy_cmd_timeout': 1, 'io_cmd_timeout': 30})
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            reply = await asyncio.wait_for(read_reply(reader), timeout=3)
            closed = await asyncio.wait_for(reader.read(1), timeout=1) == b''
            writer.close()
            return reply, closed
        finally:
            await stop_server(server, task)

    reply, closed = asyncio.run(go())
    assert reply[0].startswith('421')
    assert closed is True


def test_proxy_event_error_aborts_421():
    """An exception in on_proxy_event gets 421, no other reply."""

    class RejectingProxyEvents(ProxyEvents):

        def on_proxy_event(self, ctx, proxy_data):
            raise Smtpd550Exception

    async def go():
        return await _dialog_raw(
            RejectingProxyEvents(),
            {'proxy_extension': True},
            [(b'PROXY TCP4 1.2.3.4 5.6.7.8 1 2\r\n', True)],
            has_server_greeting=False,
        )

    _, replies, closed = asyncio.run(go())
    assert replies[0][0].startswith('421')
    assert closed is True


# --- graceful shutdown -------------------------------------------------------


def test_stop_stops_accepting_new_connections():
    """After stop a new connection is refused and stopped() is True."""

    async def go():
        srv, port = await start_smtpd(RecordingEvents(), settings={})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        greeting = await read_reply(reader)
        writer.close()
        await srv.stop(wait_seconds_before_close=0.2)
        refused = False
        try:
            await asyncio.open_connection('127.0.0.1', port)
        except OSError:
            refused = True
        return greeting, refused, srv.stopped()

    greeting, refused, stopped = asyncio.run(go())
    assert greeting[0].startswith('220')
    assert refused is True
    assert stopped is True


def test_graceful_stop_lets_the_current_command_finish():
    """A command in progress at a graceful stop still gets its reply."""

    class SlowMailEvents(RecordingEvents):

        def __init__(self, options=None):
            super().__init__(options)
            self.busy = False

        async def on_mail_from_event(self, ctx, mail_from):
            # still busy with MAIL when the stop begins
            self.busy = True
            await asyncio.sleep(0.3)
            return super().on_mail_from_event(ctx, mail_from)

    events = SlowMailEvents()

    async def go():
        srv, port = await start_smtpd(events, settings={})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await read_reply(reader)
        await send_command(reader, writer, b'EHLO me\r\n')
        writer.write(b'MAIL FROM:<a@x>\r\n')
        await writer.drain()
        assert await wait_until_async(lambda: events.busy)
        stop = asyncio.create_task(srv.stop(wait_seconds_before_close=2.0))
        mail = await asyncio.wait_for(read_reply(reader), timeout=3)
        bye = await asyncio.wait_for(read_reply(reader), timeout=3)
        await stop
        writer.close()
        return mail, bye

    mail, bye = asyncio.run(go())
    assert mail == ['250 OK']
    # RFC 5321 3.8: a shutdown closes with 421
    assert bye[0].startswith('421')


def test_shutdown_ends_a_waiting_dialog_at_once_with_421():
    """A dialog waiting for its next command gets 421 at once on shutdown."""

    async def go():
        srv, port = await start_smtpd(RecordingEvents(), settings={})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await read_reply(reader)
        await send_command(reader, writer, b'EHLO me\r\n')
        # the client sends nothing more, the shutdown must not wait for it
        srv.shutdown()
        reply = await asyncio.wait_for(read_reply(reader), timeout=2)
        closed = await asyncio.wait_for(reader.read(1), timeout=2) == b''
        writer.close()
        await srv.stop(wait_seconds_before_close=0.2)
        return reply, closed

    reply, closed = asyncio.run(go())
    assert reply[0].startswith('421')
    assert closed is True


def test_graceful_stop_ends_early_when_all_dialogs_ended():
    """A graceful stop with only waiting dialogs returns before its drain window."""

    async def go():
        srv, port = await start_smtpd(RecordingEvents(), settings={})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await read_reply(reader)
        await send_command(reader, writer, b'EHLO me\r\n')
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await srv.stop(wait_seconds_before_close=5)
        took = loop.time() - t0
        reply = await asyncio.wait_for(read_reply(reader), timeout=2)
        writer.close()
        return took, reply

    took, reply = asyncio.run(go())
    assert took < 2
    assert reply[0].startswith('421')


def test_forced_stop_sends_421_before_the_drop():
    """A dialog still busy after the drain window gets 421 before the drop."""

    class SlowEvents(RecordingEvents):

        def __init__(self, options=None):
            super().__init__(options)
            self.busy = False

        async def on_message_data_event(self, ctx):
            # busy far beyond the drain window of the stop below
            self.busy = True
            await asyncio.sleep(10)

    events = SlowEvents()

    async def go():
        srv, port = await start_smtpd(events, settings={})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await read_reply(reader)
        for cmd in (b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n'):
            await send_command(reader, writer, cmd)
        writer.write(b'body\r\n.\r\n')
        await writer.drain()
        assert await wait_until_async(lambda: events.busy)
        stop = asyncio.create_task(srv.stop(wait_seconds_before_close=0.3))
        reply = await asyncio.wait_for(read_reply(reader), timeout=3)
        await stop
        writer.close()
        return reply

    reply = asyncio.run(go())
    assert reply[0].startswith('421')


def test_stop_not_gracefully_sends_421_at_once():
    """A stop that is not graceful drops a waiting dialog at once with 421."""

    async def go():
        srv, port = await start_smtpd(RecordingEvents(), settings={})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await read_reply(reader)
        await send_command(reader, writer, b'EHLO me\r\n')
        await srv.stop(gracefully=False)
        reply = await asyncio.wait_for(read_reply(reader), timeout=2)
        writer.close()
        return reply

    reply = asyncio.run(go())
    assert reply[0].startswith('421')


def test_shutdown_refuses_ungreeted_connection():
    """After shutdown a new connection gets 421 instead of 220."""

    async def go():
        srv, port = await start_smtpd(RecordingEvents(), settings={})
        # a scheduled shutdown greets a new connection with 421, not 220
        srv.shutdown()
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        reply = await asyncio.wait_for(read_reply(reader), timeout=2)
        writer.close()
        await srv.stop(wait_seconds_before_close=0.2)
        return reply

    reply = asyncio.run(go())
    assert reply[0].startswith('421')


def test_graceful_stop_cancels_idle_connection():
    """stop ends an idle connection, no session is left."""

    async def go():
        srv, port = await start_smtpd(RecordingEvents(), settings={})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await read_reply(reader)
        active_before = len(srv._sessions)
        # the connection is idle (blocked reading); a graceful stop drains it
        await srv.stop(wait_seconds_before_close=0.3)
        writer.close()
        return active_before, srv.stopped(), len(srv._sessions)

    active_before, stopped, active_after = asyncio.run(go())
    assert active_before == 1
    assert stopped is True
    assert active_after == 0


def test_lifecycle_state_flags():
    """stopped() and shutdown_requested() follow start, shutdown and stop."""

    async def go():
        srv = SmtpdServer(RecordingEvents(), settings={})
        before = srv.stopped()
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        serving = srv.stopped()
        req_before = srv.shutdown_requested()
        srv.shutdown()
        req_after = srv.shutdown_requested()
        await srv.stop(wait_seconds_before_close=0.1)
        after = srv.stopped()
        return before, serving, req_before, req_after, after

    before, serving, req_before, req_after, after = asyncio.run(go())
    assert before is True
    assert serving is False
    assert req_before is False and req_after is True
    assert after is True


# --- AUTH (PLAIN / LOGIN) ------------------------------------------------------


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def test_auth_forbidden_by_default_is_500():
    """By default AUTH answers 500."""
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'AUTH PLAIN ' + auth_plain('', 'u', 'p').encode() + b'\r\n'])
    assert r[1][0].startswith('500')


def test_ehlo_advertises_auth_when_enabled():
    """With AUTH_OPTIONAL EHLO lists "AUTH LOGIN PLAIN"."""
    _, r = dialog(AuthEvents(), {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n'])
    assert any('AUTH LOGIN PLAIN' in ln for ln in r[0])


def test_auth_plain_inline_success_and_ctx():
    """AUTH PLAIN inline answers 235, the ctx carries the auth info."""
    ev = AuthEvents()
    _, r = dialog(
        ev,
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'EHLO me\r\n',
            b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n',
            b'MAIL FROM:<a@x>\r\n',
        ],
    )
    assert r[1][0] == '235 OK'
    assert r[2][0] == '250 OK'
    # event saw authzid/authcid/password; ctx carries the auth info afterwards
    assert ev.auth_calls == [('', 'user', 'secret')]
    authenticated, authentication_id, authorization_id = ev.ctx_auth
    assert authenticated and authentication_id == 'user'
    # the authorization id is the one returned by the handler
    assert authorization_id == 'user'


def test_auth_plain_wrong_credentials_535():
    """AUTH PLAIN with a wrong password answers 535."""
    ev = AuthEvents()
    _, r = dialog(
        ev,
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'EHLO me\r\n',
            b'AUTH PLAIN ' + auth_plain('', 'user', 'WRONG').encode() + b'\r\n',
        ],
    )
    assert r[1][0].startswith('535')


def test_auth_plain_prompt_form():
    """AUTH PLAIN without data prompts by "334 ", then answers 235."""
    ev = AuthEvents()
    _, r = dialog(
        ev,
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'EHLO me\r\n',
            b'AUTH PLAIN\r\n',
            auth_plain('', 'user', 'secret').encode() + b'\r\n',
        ],
    )
    # the bare AUTH PLAIN is answered with '334 ' (space included)
    assert r[1][0].rstrip() == '334'
    assert r[2][0] == '235 OK'


def test_auth_login_challenge_full():
    """AUTH LOGIN asks for user and password in base64, then 235."""
    ev = AuthEvents()
    _, r = dialog(
        ev,
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'EHLO me\r\n',
            b'AUTH LOGIN\r\n',
            _b64('user').encode() + b'\r\n',
            _b64('secret').encode() + b'\r\n',
        ],
    )
    assert r[1][0] == '334 ' + _b64('Username:')
    assert r[2][0] == '334 ' + _b64('Password:')
    assert r[3][0] == '235 OK'
    assert ev.auth_calls == [('', 'user', 'secret')]


def test_auth_login_with_inline_user():
    """AUTH LOGIN with the user inline asks for the password only."""
    ev = AuthEvents()
    _, r = dialog(
        ev,
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'EHLO me\r\n',
            b'AUTH LOGIN ' + _b64('user').encode() + b'\r\n',
            _b64('secret').encode() + b'\r\n',
        ],
    )
    assert r[1][0] == '334 ' + _b64('Password:')
    assert r[2][0] == '235 OK'


def test_auth_default_handler_denies_535():
    """The default handler denies every AUTH by 535."""
    _, r = dialog(
        RecordingEvents(),
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'EHLO me\r\n',
            b'AUTH PLAIN ' + auth_plain('', 'any', 'thing').encode() + b'\r\n',
        ],
    )
    assert r[1][0].startswith('535')


def test_auth_garbage_base64_500_and_recovers():
    """Invalid credentials answer 500, MAIL works after it."""
    ev = AuthEvents()
    _, r = dialog(
        ev,
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'EHLO me\r\n',
            b'AUTH PLAIN\r\n',
            b'!!!not-base64!!!\r\n',  # not valid credentials -> 500
            b'MAIL FROM:<a@x>\r\n',  # sequence was reset to RSET, MAIL works
        ],
    )
    assert r[2][0].startswith('500')
    assert r[3][0] == '250 OK'


@pytest.mark.parametrize(
    'steps',
    [
        [b'AUTH PLAIN\r\n'],
        [b'AUTH LOGIN\r\n'],
        [b'AUTH LOGIN\r\n', _b64('user').encode() + b'\r\n'],
    ],
    ids=['plain', 'login-user', 'login-password'],
)
def test_auth_cancel_answers_501_and_recovers(steps):
    """A single "*" cancels the AUTH exchange with 501, the dialog goes on."""
    ev = AuthEvents()
    _, r = dialog(
        ev,
        {'auth_mode': 'AUTH_OPTIONAL'},
        [b'EHLO me\r\n', *steps, b'*\r\n', b'MAIL FROM:<a@x>\r\n'],
    )
    assert r[-2][0].startswith('501')
    assert r[-1][0] == '250 OK'
    assert ev.auth_calls == []


def test_auth_plain_inline_with_plus_and_slash():
    """An initial response whose base64 holds "+" and "/" is accepted."""
    ev = AuthEvents()
    # the authzid '>>>???' encodes to 'Pj4+Pz8/' at the start of the base64
    response = auth_plain('>>>???', 'user', 'secret')
    assert '+' in response and '/' in response
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n', b'AUTH PLAIN ' + response.encode() + b'\r\n'])
    assert r[1][0] == '235 OK'
    assert ev.auth_calls == [('>>>???', 'user', 'secret')]


def test_auth_before_helo_503():
    """AUTH before HELO answers 503."""
    _, r = dialog(
        AuthEvents(),
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n',
        ],
    )
    assert r[0][0].startswith('503')


def test_auth_twice_503():
    """A second AUTH answers 503."""
    ev = AuthEvents()
    _, r = dialog(
        ev,
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'EHLO me\r\n',
            b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n',
            b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n',
        ],
    )
    assert r[1][0] == '235 OK'
    assert r[2][0].startswith('503')


@pytest.mark.parametrize('mechanism', [b'CRAM-MD5', b'DIGEST-MD5', b'XPLAINX'])
def test_auth_unsupported_mechanism_504(mechanism):
    """AUTH with a mechanism the server does not support answers 504."""
    _, r = dialog(AuthEvents(), {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n', b'AUTH ' + mechanism + b'\r\n'])
    assert r[1][0].startswith('504')


def test_auth_required_gates_mail():
    """With AUTH_REQUIRED MAIL answers 530 until AUTH succeeded."""
    ev = AuthEvents()
    _, r = dialog(
        ev,
        {'auth_mode': 'AUTH_REQUIRED'},
        [
            b'EHLO me\r\n',
            b'MAIL FROM:<a@x>\r\n',  # not authenticated -> 530
            b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n',
            b'MAIL FROM:<a@x>\r\n',  # authenticated -> 250
        ],
    )
    assert r[1][0].startswith('530')
    assert r[2][0] == '235 OK'
    assert r[3][0] == '250 OK'


def test_auth_authzid_returned_by_handler():
    """The id returned by on_auth_event becomes the authorization id."""
    ev = AuthEvents()
    ev.authz_return = 'boss'
    dialog(
        ev,
        {'auth_mode': 'AUTH_OPTIONAL'},
        [
            b'EHLO me\r\n',
            b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n',
            b'MAIL FROM:<a@x>\r\n',
        ],
    )
    # the handler's return value becomes the authorization id
    assert ev.ctx_auth[2] == 'boss'


@pytest.mark.parametrize('authz_return', [None, '', True, 1])
@pytest.mark.parametrize('mechanism', ['plain', 'login'])
def test_auth_without_returned_authzid_535(mechanism, authz_return):
    """A handler result other than a non-empty string denies by 535."""
    ev = AuthEvents()
    ev.authz_return = authz_return
    if mechanism == 'plain':
        # the authzid of the client is never taken over on its own
        auth = [b'AUTH PLAIN ' + auth_plain('boss', 'user', 'secret').encode() + b'\r\n']
    else:
        auth = [b'AUTH LOGIN ' + _b64('user').encode() + b'\r\n', _b64('secret').encode() + b'\r\n']
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n', *auth, b'MAIL FROM:<a@x>\r\n'])
    assert r[-2][0].startswith('535')
    # not authenticated, MAIL still possible with AUTH_OPTIONAL
    assert r[-1][0] == '250 OK'
    assert ev.ctx_auth == ('', '', '')


@pytest.mark.parametrize('authz_return', [' user', 'user ', '  '])
def test_auth_not_stripped_authzid_is_a_handler_fault(authz_return):
    """A returned authorization id with surrounding whitespace answers 454."""
    ev = AuthEvents()
    ev.authz_return = authz_return
    auth = b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n'
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n', auth, b'MAIL FROM:<a@x>\r\n'])
    assert r[1][0].startswith('454')
    assert r[2][0] == '250 OK'
    assert ev.ctx_auth == ('', '', '')


@pytest.mark.parametrize('mechanism', ['plain', 'login'])
def test_auth_handler_crash_454(mechanism):
    """An unexpected exception of on_auth_event answers 454, AUTH may follow."""

    class E(AuthEvents):

        def on_auth_event(self, ctx, authorization_id, authentication_id, authentication):
            if not self.auth_calls:
                self.auth_calls.append('down')
                raise ConnectionError('user database down')
            return super().on_auth_event(ctx, authorization_id, authentication_id, authentication)

    if mechanism == 'plain':
        auth = [b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n']
    else:
        auth = [b'AUTH LOGIN ' + _b64('user').encode() + b'\r\n', _b64('secret').encode() + b'\r\n']
    ev = E()
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n', *auth, *auth])
    assert r[len(auth)][0] == '454 Temporary authentication failure'
    assert r[-1][0] == '235 OK'


class AuthForEvents(AuthEvents):
    """Records the sender and the AUTH parameter of every MAIL."""

    def __init__(self, options=None):
        super().__init__(options)
        self.mails = []

    def on_mail_from_event(self, ctx, mail_from):
        self.mails.append((mail_from, ctx.envelope.auth_for_id, ctx.envelope.auth_for_id_raw))
        return super().on_mail_from_event(ctx, mail_from)


def _auth_for(trusted, login, mails):
    """Runs the MAILs, logged in as "user" or not, and returns them recorded."""
    ev = AuthForEvents()
    auth = [b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n'] if login else []
    settings = {'auth_mode': 'AUTH_OPTIONAL', 'trusted_relay_auth_ids': trusted}
    commands = [b'EHLO me\r\n', *auth]
    for mail in mails:
        commands += [b'MAIL FROM:' + mail + b'\r\n', b'RSET\r\n']
    _, r = dialog(ev, settings, commands)
    # every MAIL and RSET is accepted
    assert [line[0] for line in r].count('250 OK') == 2 * len(mails)
    return ev.mails


@pytest.mark.parametrize(
    'trusted, login, param, auth_for_id, auth_for_id_raw',
    [
        # trusted relay: the decoded xtext names the submitter
        (['user'], True, b'AUTH=e+3Dmc2@example.com', 'e=mc2@example.com', 'e=mc2@example.com'),
        (['user'], True, b'auth=e+3dmc2@example.com', 'e=mc2@example.com', 'e=mc2@example.com'),
        # surrounding whitespace of the decoded xtext is stripped
        (['user'], True, b'AUTH=+20alice@x+20', 'alice@x', ' alice@x '),
        (['user'], True, b'AUTH=+20+20', '', '  '),
        (['user'], True, b'AUTH=+20<>+20', '', ' <> '),
        # unknown submitter, also from a trusted relay
        (['user'], True, b'AUTH=<>', '', '<>'),
        # logged in, but not as a trusted relay
        ([], True, b'AUTH=bob@example.com', '', 'bob@example.com'),
        (['relay'], True, b'AUTH=bob@example.com', '', 'bob@example.com'),
        # not logged in
        (['user'], False, b'AUTH=bob@example.com', '', 'bob@example.com'),
    ],
)
def test_mail_from_auth_parameter(trusted, login, param, auth_for_id, auth_for_id_raw):
    """AUTH= leaves the sender, auth_for_id carries it only from a trusted relay."""
    mails = _auth_for(trusted, login, [b'<a@x> ' + param])
    assert mails == [('<a@x>', auth_for_id, auth_for_id_raw)]


def test_mail_from_auth_parameter_ends_with_the_transaction():
    """A next MAIL without AUTH= starts with empty auth_for_id fields."""
    mails = _auth_for(['user'], True, [b'<a@x> AUTH=s@example.com', b'<b@x>'])
    assert mails == [('<a@x>', 's@example.com', 's@example.com'), ('<b@x>', '', '')]


# --- STARTTLS --------------------


class TlsEvents(RecordingEvents):
    """Records whether the connection was encrypted at MAIL time."""

    def __init__(self, options=None):
        super().__init__(options)
        self.encrypted_at_mail = None

    def on_mail_from_event(self, ctx, mail_from):
        self.encrypted_at_mail = ctx.server.encrypted
        return super().on_mail_from_event(ctx, mail_from)


def test_starttls_full_flow_delivers_over_tls():
    """STARTTLS answers 220, EHLO then omits it and the mail is delivered."""
    ev = TlsEvents()

    async def go():
        server, port, task = await start_server(ev, TLS_SETTINGS)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            ehlo1 = await send_command(reader, writer, b'EHLO me\r\n')
            tls_reply = await send_command(reader, writer, b'STARTTLS\r\n')
            await writer.start_tls(insecure_tls_context())
            ehlo2 = await send_command(reader, writer, b'EHLO secure\r\n')
            for cmd in (b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n'):
                await send_command(reader, writer, cmd)
            writer.write(b'body over tls\r\n.\r\n')
            await writer.drain()
            final = await read_reply(reader)
            writer.close()
            await writer.wait_closed()
            return ehlo1, tls_reply, ehlo2, final
        finally:
            await stop_server(server, task)

    ehlo1, tls_reply, ehlo2, final = asyncio.run(go())
    assert any('STARTTLS' in ln for ln in ehlo1)
    assert tls_reply[0] == '220 Ready to start TLS'
    # already encrypted -> STARTTLS no longer advertised
    assert not any('STARTTLS' in ln for ln in ehlo2)
    assert final[0].startswith('250')
    assert ev.messages and ev.messages[0][0] == b'body over tls'
    assert ev.encrypted_at_mail


def test_starttls_discards_a_plaintext_auth():
    """STARTTLS drops an AUTH of the plaintext channel, AUTH works again."""
    ev = AuthEvents()
    plain = b'AUTH PLAIN ' + auth_plain('', 'user', 'secret').encode() + b'\r\n'

    async def go():
        server, port, task = await start_server(ev, {**TLS_SETTINGS, 'auth_mode': 'AUTH_OPTIONAL'})
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            await send_command(reader, writer, b'EHLO me\r\n')
            first = await send_command(reader, writer, plain)
            await send_command(reader, writer, b'STARTTLS\r\n')
            await writer.start_tls(insecure_tls_context())
            await send_command(reader, writer, b'EHLO secure\r\n')
            second = await send_command(reader, writer, plain)
            writer.close()
            return first, second
        finally:
            await stop_server(server, task)

    first, second = asyncio.run(go())
    assert first == ['235 OK']
    # without the reset the second AUTH would answer 503
    assert second == ['235 OK']
    assert len(ev.auth_calls) == 2


def test_starttls_forbidden_is_500():
    """By default STARTTLS answers 500."""
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'STARTTLS\r\n'])
    assert r[1][0].startswith('500')


def test_starttls_before_helo_is_503():
    """STARTTLS before HELO answers 503."""
    _, r = dialog(RecordingEvents(), TLS_SETTINGS, [b'STARTTLS\r\n'])
    assert r[0][0].startswith('503')


def test_starttls_twice_already_encrypted_503():
    """STARTTLS on an encrypted connection answers 503."""

    async def go():
        server, port, task = await start_server(RecordingEvents(), TLS_SETTINGS)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            await send_command(reader, writer, b'EHLO me\r\n')
            await send_command(reader, writer, b'STARTTLS\r\n')
            await writer.start_tls(insecure_tls_context())
            await send_command(reader, writer, b'EHLO secure\r\n')
            second = await send_command(reader, writer, b'STARTTLS\r\n')
            writer.close()
            return second
        finally:
            await stop_server(server, task)

    second = asyncio.run(go())
    assert second[0].startswith('503')


def test_tls_required_gates_mail_until_starttls():
    """With TLS_REQUIRED MAIL answers 530 until STARTTLS."""

    async def go():
        server, port, task = await start_server(RecordingEvents(), dict(TLS_SETTINGS, encrypt_mode='TLS_REQUIRED'))
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            await send_command(reader, writer, b'EHLO me\r\n')
            before = await send_command(reader, writer, b'MAIL FROM:<a@x>\r\n')
            await send_command(reader, writer, b'STARTTLS\r\n')
            await writer.start_tls(insecure_tls_context())
            await send_command(reader, writer, b'EHLO secure\r\n')
            after = await send_command(reader, writer, b'MAIL FROM:<a@x>\r\n')
            writer.close()
            return before, after
        finally:
            await stop_server(server, task)

    before, after = asyncio.run(go())
    assert before[0].startswith('530')
    assert after[0] == '250 OK'


def test_starttls_flushes_injected_plaintext():
    """
    A QUIT sent right behind STARTTLS in plaintext is discarded, also with
    PIPELINING: the connection stays and EHLO over TLS answers 250.
    """

    async def go():
        server, port, task = await start_server(RecordingEvents(), dict(TLS_SETTINGS, pipelining_extension=True))
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            await send_command(reader, writer, b'EHLO me\r\n')
            # STARTTLS plus an injected QUIT in the same plaintext segment
            writer.write(b'STARTTLS\r\nQUIT\r\n')
            await writer.drain()
            tls_reply = await read_reply(reader)
            await writer.start_tls(insecure_tls_context())
            ehlo = await send_command(reader, writer, b'EHLO secure\r\n')
            writer.close()
            return tls_reply, ehlo
        finally:
            await stop_server(server, task)

    tls_reply, ehlo = asyncio.run(go())
    assert tls_reply[0] == '220 Ready to start TLS'
    # the injected QUIT was flushed: the session is alive and EHLO works
    assert ehlo[-1] == '250 OK'
