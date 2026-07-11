"""
Tests for tokeo.ext.smtpd.server (the own asyncio SMTP server, no TLS/AUTH).

Everything is exercised over a real SMTP conversation against a live server:
the full dialog and reference-faithful behaviour (null sender, de-dot-stuffing,
byte-exact body, multiple transactions, sequence enforcement, handler rejects),
the security enforcement we own (PIPELINING rejection, CRLF strictness, data_size,
buffer overrun, command timeout), and the connection/processing limits.
"""

import ssl
import base64
import asyncio

import pytest

from tokeo.core.smtpd.server import SmtpdServer
from tokeo.core.smtpd.events import SmtpdEvents, threaded
from tokeo.core.smtpd.exc import Smtpd550Exception, Smtpd450Exception, Smtpd421Exception


class RecordingEvents(SmtpdEvents):
    """A handler that records fired events and delivered messages."""

    def __init__(self, options=None):
        super().__init__(options)
        self.fired = []
        self.messages = []
        self.reject_rcpt = set()
        self.rewrite_from = None

    def on_connect_event(self, ctx):
        self.fired.append('connect')

    def on_disconnect_event(self, ctx):
        self.fired.append('disconnect')

    def on_helo_event(self, ctx, helo):
        self.fired.append('helo')

    def on_mail_from_event(self, ctx, mail_from):
        self.fired.append('mail')
        return self.rewrite_from

    def on_rcpt_to_event(self, ctx, rcpt_to):
        self.fired.append('rcpt')
        if any(s in rcpt_to for s in self.reject_rcpt):
            raise Smtpd550Exception

    def on_message_data_start_event(self, ctx):
        self.fired.append('start')

    def on_message_data_headers_event(self, ctx):
        self.fired.append('headers')

    def on_message_data_receiving_event(self, ctx):
        self.fired.append('recv')

    def on_message_data_event(self, ctx):
        self.messages.append((bytes(ctx.message.data), ctx.envelope.mail_from, list(ctx.envelope.rcpt_tos)))


async def _reply(reader):
    """Read a full (possibly multi-line) SMTP reply into a list of lines."""
    lines = []
    while True:
        raw = await reader.readuntil(b'\n')
        line = raw.decode('latin-1').rstrip('\r\n')
        lines.append(line)
        if len(line) < 4 or line[3] != '-':
            break
    return lines


async def _serve(events, settings):
    """Start a server on an ephemeral port; return (server, port, task)."""
    srv = SmtpdServer(events, settings=settings)
    limit = int(settings.get('io_buffer_max_size', 1 << 20))
    server = await asyncio.start_server(srv._on_connection, '127.0.0.1', 0, limit=limit)
    port = server.sockets[0].getsockname()[1]
    task = asyncio.create_task(server.serve_forever())
    return server, port, task


async def _stop(server, task):
    """Close a server and cancel its serve task."""
    server.close()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _converse(events, settings, commands):
    """Run one client dialog: send each command, read its reply."""
    server, port, task = await _serve(events, settings)
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        banner = await _reply(reader)
        replies = []
        for cmd in commands:
            writer.write(cmd)
            await writer.drain()
            replies.append(await _reply(reader))
        writer.close()
        await asyncio.sleep(0.02)
        return banner, replies
    finally:
        await _stop(server, task)


def dialog(events, settings, commands):
    """Synchronous wrapper around a client dialog for pytest."""
    return asyncio.run(_converse(events, settings, commands))


# --- full delivery and events ------------------------------------------------


def test_full_delivery_and_events():
    ev = RecordingEvents()
    banner, r = dialog(ev, {}, [
        b'EHLO me\r\n',
        b'MAIL FROM:<a@x>\r\n',
        b'RCPT TO:<b@y>\r\n',
        b'DATA\r\n',
        b'Subject: hi\r\n\r\nHello world\r\n.\r\n',
        b'QUIT\r\n',
    ])
    assert banner[0].startswith('220')
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
    _, r = dialog(RecordingEvents(), {}, [b'HELO me\r\n'])
    assert r[0][0].startswith('250 ') and len(r[0]) == 1


def test_ehlo_advertises_extensions_when_enabled():
    settings = {'internationalization_extensions': True, 'pipelining_extension': True}
    _, r = dialog(RecordingEvents(), settings, [b'EHLO me\r\n'])
    text = '\n'.join(r[0])
    assert '8BITMIME' in text and 'SMTPUTF8' in text and 'PIPELINING' in text
    assert r[0][-1] == '250 OK'


# --- reference-faithful behaviour --------------------------------------------


def test_null_sender_accepted():
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'MAIL FROM:<>\r\n'])
    assert r[1] == ['250 OK']


def test_de_dot_stuffing_and_byte_exact_body():
    ev = RecordingEvents()
    dialog(ev, {}, [
        b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n',
        b'..dotted\r\n.leading\r\nplain\r\n.\r\n',
    ])
    # a leading dot is stripped from each line; the final CRLF is chomped
    assert ev.messages[0][0] == b'.dotted\r\nleading\r\nplain'


def test_multiple_transactions_with_rset():
    ev = RecordingEvents()
    _, r = dialog(ev, {}, [
        b'EHLO me\r\n',
        b'MAIL FROM:<a@x>\r\n', b'RSET\r\n',
        b'MAIL FROM:<c@z>\r\n', b'RCPT TO:<d@z>\r\n', b'DATA\r\n', b'body\r\n.\r\n',
    ])
    assert r[2] == ['250 OK']  # RSET
    assert r[6][0].startswith('250')
    # after RSET the second transaction is the only delivered message
    assert ev.messages[0][1] == '<c@z>' and ev.messages[0][2] == ['<d@z>']


def test_mail_from_rewrite_by_handler():
    ev = RecordingEvents()
    ev.rewrite_from = '<rewritten@x>'
    ev.messages.clear()
    dialog(ev, {}, [
        b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n', b'x\r\n.\r\n',
    ])
    assert ev.messages[0][1] == '<rewritten@x>'


def test_noop_and_unknown_command():
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'NOOP\r\n', b'FOOBAR\r\n'])
    assert r[1] == ['250 OK']
    assert r[2][0].startswith('500')


# --- sequence and reject codes ---------------------------------


def test_sequence_rcpt_before_mail_is_503():
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'RCPT TO:<x>\r\n'])
    assert r[1][0].startswith('503')


def test_sequence_data_before_rcpt_is_503():
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'DATA\r\n'])
    assert r[2][0].startswith('503')


def test_mail_before_helo_is_503():
    _, r = dialog(RecordingEvents(), {}, [b'MAIL FROM:<a@x>\r\n'])
    assert r[0][0].startswith('503')


def test_handler_reject_maps_to_550():
    ev = RecordingEvents()
    ev.reject_rcpt = {'deny'}
    _, r = dialog(ev, {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<deny@x>\r\n'])
    assert r[2][0].startswith('550')


def test_handler_reject_transient_450():
    class E(RecordingEvents):
        def on_rcpt_to_event(self, ctx, rcpt_to):
            raise Smtpd450Exception

    _, r = dialog(E(), {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n'])
    assert r[2][0].startswith('450')


def test_handler_bug_contained_as_500():
    class E(RecordingEvents):
        def on_mail_from_event(self, ctx, mail_from):
            raise RuntimeError('boom')

    _, r = dialog(E(), {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n'])
    assert r[1][0].startswith('500')


# --- security we own ---------------------------------------------------------


def test_pipelining_rejected_when_disabled():
    async def go():
        server, port, task = await _serve(RecordingEvents(), {})
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await _reply(reader)
            writer.write(b'EHLO me\r\n')
            await writer.drain()
            await _reply(reader)
            # two commands in one segment -> the first sees a buffered next line
            writer.write(b'NOOP\r\nNOOP\r\n')
            await writer.drain()
            first = await _reply(reader)
            writer.close()
            return first
        finally:
            await _stop(server, task)

    first = asyncio.run(go())
    assert first[0].startswith('500')


def test_pipelining_allowed_when_enabled():
    async def go():
        server, port, task = await _serve(RecordingEvents(), {'pipelining_extension': True})
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await _reply(reader)
            writer.write(b'EHLO me\r\n')
            await writer.drain()
            await _reply(reader)
            writer.write(b'NOOP\r\nNOOP\r\n')
            await writer.drain()
            a = await _reply(reader)
            b = await _reply(reader)
            writer.close()
            return a, b
        finally:
            await _stop(server, task)

    a, b = asyncio.run(go())
    assert a == ['250 OK'] and b == ['250 OK']


def test_crlf_strict_rejects_bare_lf():
    _, r = dialog(RecordingEvents(), {'crlf_mode': 'CRLF_STRICT'}, [b'EHLO me\n'])
    assert r[0][0].startswith('500')


def test_data_size_limit_552():
    ev = RecordingEvents()
    _, r = dialog(ev, {'data_size': 50}, [
        b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n',
        b'X' * 200 + b'\r\n',
    ])
    assert r[4][0].startswith('552')


def test_buffer_overrun_421():
    # the overrun raises to the outer handler which answers 421 and drops
    _, r = dialog(RecordingEvents(), {'io_buffer_max_size': 100}, [b'EHLO ' + b'a' * 300 + b'\r\n'])
    assert r[0][0].startswith('421')


def test_command_timeout_closes_connection():
    async def go():
        server, port, task = await _serve(RecordingEvents(), {'io_cmd_timeout': 0.2})
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await _reply(reader)
            # send nothing; the server must time out and answer 421
            reply = await asyncio.wait_for(_reply(reader), timeout=2)
            writer.close()
            return reply
        finally:
            await _stop(server, task)

    reply = asyncio.run(go())
    assert reply[0].startswith('421')


def test_read_line_skips_wait_for_when_line_buffered(monkeypatch):
    # the timeout guards waiting on the wire; with a complete line already in
    # the reader buffer, _read_line must take the direct path (no wait_for)
    from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents

    async def go():
        srv = SmtpdServer(CaptureSmtpdEvents(), settings={'io_cmd_timeout': 30})
        reader = asyncio.StreamReader()
        reader.feed_data(b'HELO x\r\nMAIL FROM:<a@b>\r\n')

        def boom(*args, **kwargs):
            raise AssertionError('wait_for must not be used when a line is buffered')

        monkeypatch.setattr(asyncio, 'wait_for', boom)
        assert await srv._read_line(reader) == b'HELO x\r\n'
        assert await srv._read_line(reader) == b'MAIL FROM:<a@b>\r\n'

    asyncio.run(go())


def test_read_line_times_out_on_partial_buffered_line():
    # a partial line (no LF) is not "delivered": the wait_for path must apply
    # and the io_cmd_timeout must fire exactly as before
    from tokeo.core.smtpd.exc import SmtpdIOTimeoutException
    from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents

    async def go():
        srv = SmtpdServer(CaptureSmtpdEvents(), settings={'io_cmd_timeout': 0.1})
        reader = asyncio.StreamReader()
        reader.feed_data(b'incomplete without newline')
        with pytest.raises(SmtpdIOTimeoutException):
            await srv._read_line(reader)

    asyncio.run(go())


# --- internationalization ----------------------------------------------------


def test_body_and_smtputf8_params_stripped():
    ev = RecordingEvents()
    dialog(ev, {'internationalization_extensions': True}, [
        b'EHLO me\r\n', b'MAIL FROM:<a@x> BODY=8BITMIME SMTPUTF8\r\n',
        b'RCPT TO:<b@y>\r\n', b'DATA\r\n', b'x\r\n.\r\n',
    ])
    # the address reaches the handler without the params
    assert ev.messages[0][1] == '<a@x>'


def test_body_param_rejected_without_intl():
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'MAIL FROM:<a@x> BODY=8BITMIME\r\n'])
    assert r[1][0].startswith('501')


# --- limits ------------------------------------------------------------------


def test_max_connections_refuses_with_421():
    async def go():
        server, port, task = await _serve(RecordingEvents(), {'max_connections': 1, 'max_processings': 1})
        try:
            r1, w1 = await asyncio.open_connection('127.0.0.1', port)
            b1 = await _reply(r1)
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            b2 = await _reply(r2)
            w1.close()
            w2.close()
            return b1, b2
        finally:
            await _stop(server, task)

    b1, b2 = asyncio.run(go())
    assert b1[0].startswith('220')
    assert b2[0].startswith('421')


def test_threaded_event_runs_off_loop():
    class E(RecordingEvents):
        @threaded
        def on_message_data_event(self, ctx):
            self.messages.append((bytes(ctx.message.data), ctx.envelope.mail_from, list(ctx.envelope.rcpt_tos)))

    ev = E()
    _, r = dialog(ev, {}, [
        b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n', b'hi\r\n.\r\n',
    ])
    assert r[4][0].startswith('250')
    assert ev.messages and ev.messages[0][0] == b'hi'


@pytest.mark.parametrize('mode', ['CRLF_ENSURE', 'CRLF_LEAVE', 'CRLF_STRICT'])
def test_delivery_works_in_all_crlf_modes(mode):
    ev = RecordingEvents()
    _, r = dialog(ev, {'crlf_mode': mode}, [
        b'EHLO me\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n', b'body\r\n.\r\n',
    ])
    assert r[4][0].startswith('250')
    assert ev.messages[0][0].rstrip(b'\r\n') == b'body'


def test_on_connect_fires_even_when_refused():
    # contract: every accepted connection hits on_connect_event, even one
    # that is then refused with 421 for exceeding max_connections
    ev = RecordingEvents()

    async def go():
        server, port, task = await _serve(ev, {'max_connections': 1, 'max_processings': 1})
        try:
            r1, w1 = await asyncio.open_connection('127.0.0.1', port)
            await _reply(r1)
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            b2 = await _reply(r2)
            w1.close()
            w2.close()
            await asyncio.sleep(0.05)
            return b2
        finally:
            await _stop(server, task)

    b2 = asyncio.run(go())
    assert b2[0].startswith('421')
    assert ev.fired.count('connect') == 2


def test_max_processings_waits_for_free_slot():
    # a new client waits (before its 220 banner) until processings drops
    # below max_processings; with a single slot the second client is blocked
    async def go():
        server, port, task = await _serve(RecordingEvents(), {'max_processings': 1})
        try:
            r1, w1 = await asyncio.open_connection('127.0.0.1', port)
            b1 = await _reply(r1)
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            blocked = False
            try:
                await asyncio.wait_for(_reply(r2), timeout=0.3)
            except asyncio.TimeoutError:
                blocked = True
            # free the slot by ending the first dialog
            w1.write(b'QUIT\r\n')
            await w1.drain()
            await _reply(r1)
            w1.close()
            b2 = await asyncio.wait_for(_reply(r2), timeout=2)
            w2.close()
            return b1, blocked, b2
        finally:
            await _stop(server, task)

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

    def on_proxy_event(self, ctx, proxy_data):
        self.proxy_seen = dict(proxy_data)
        return self.proxy_rewrite

    def on_helo_event(self, ctx, helo):
        # captured after PROXY, so ctx.server.proxy reflects what was stored
        self.proxy_on_ctx = ctx.server.proxy


async def _dialog_raw(events, settings, steps):
    """
    Run a dialog where each step says whether to read a reply.

    ### Args

    - **steps** (list): tuples ```(bytes_to_send, expect_reply)```; when
        expect_reply is False nothing is read (PROXY answers with silence)

    ### Returns

    - **tuple**: ```(banner, replies, closed)``` -- replies for the steps that
        expected one, and whether the server closed the connection at the end

    """
    server, port, task = await _serve(events, settings)
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        banner = await _reply(reader)
        replies = []
        for chunk, expect in steps:
            writer.write(chunk)
            await writer.drain()
            if expect:
                replies.append(await _reply(reader))
        closed = False
        try:
            tail = await asyncio.wait_for(reader.read(1), timeout=0.2)
            closed = tail == b''
        except asyncio.TimeoutError:
            closed = False
        writer.close()
        await asyncio.sleep(0.02)
        return banner, replies, closed
    finally:
        await _stop(server, task)


def test_proxy_disabled_is_unknown_command():
    # without proxy_extension the PROXY line is just an unknown command
    _, r = dialog(RecordingEvents(), {}, [b'PROXY TCP4 1.2.3.4 5.6.7.8 111 222\r\n'])
    assert r[0][0].startswith('500')


def test_unknown_command_counts_and_allows_abuse_abort():
    # an unknown command raises Smtpd500 internally, so the serve
    # loop counts it in ctx.server.exceptions -- which lets a handler abort an
    # abusive peer. Two unknowns answer 500; the third sees exceptions >= 2 and 421.
    class AbuseGuard(RecordingEvents):
        def on_process_line_unknown_event(self, ctx, line):
            if ctx.server.exceptions >= 2:
                raise Smtpd421Exception

    _, r = dialog(AbuseGuard(), {}, [b'FOO one\r\n', b'BAR two\r\n', b'BAZ three\r\n'])
    assert r[0][0].startswith('500')
    assert r[1][0].startswith('500')
    assert any(line.startswith('421') for line in r[2])


def test_proxy_tcp4_fills_ctx_and_fires_event():
    ev = ProxyEvents()

    async def go():
        # PROXY answers with silence; the following EHLO gets the 250
        banner, replies, closed = await _dialog_raw(
            ev,
            {'proxy_extension': True},
            [(b'PROXY TCP4 1.2.3.4 5.6.7.8 1111 2222\r\n', False), (b'EHLO me\r\n', True)],
        )
        return replies

    replies = asyncio.run(go())
    assert replies[0][-1] == '250 OK'
    assert ev.proxy_seen is not None
    assert ev.proxy_seen['proto'] == 'TCP4'
    assert ev.proxy_seen['source_ip'] == '1.2.3.4' and ev.proxy_seen['source_port'] == 1111
    assert ev.proxy_seen['dest_ip'] == '5.6.7.8' and ev.proxy_seen['dest_port'] == 2222
    # the parsed dict is stored on the ctx for later events
    assert ev.proxy_on_ctx['source_ip'] == '1.2.3.4' and ev.proxy_on_ctx['proto'] == 'TCP4'


def test_proxy_unknown_is_accepted():
    ev = ProxyEvents()

    async def go():
        return await _dialog_raw(
            ev, {'proxy_extension': True},
            [(b'PROXY UNKNOWN\r\n', False), (b'EHLO me\r\n', True)],
        )

    _, replies, _ = asyncio.run(go())
    assert replies[0][-1] == '250 OK'
    assert ev.proxy_seen['proto'] == 'UNKNOWN' and ev.proxy_seen['source_ip'] is None


def test_proxy_event_can_rewrite():
    ev = ProxyEvents()
    ev.proxy_rewrite = {'proto': 'TCP4', 'source_ip': '9.9.9.9'}

    async def go():
        return await _dialog_raw(
            ev, {'proxy_extension': True},
            [(b'PROXY TCP4 1.2.3.4 5.6.7.8 1111 2222\r\n', False), (b'EHLO me\r\n', True)],
        )

    asyncio.run(go())
    # the event saw the parsed data, but its return value replaced what is stored
    assert ev.proxy_seen['source_ip'] == '1.2.3.4'
    assert ev.proxy_on_ctx == {'proto': 'TCP4', 'source_ip': '9.9.9.9'}


def test_proxy_after_helo_is_503():
    _, r = dialog(
        ProxyEvents(), {'proxy_extension': True},
        [b'EHLO me\r\n', b'PROXY TCP4 1.2.3.4 5.6.7.8 1 2\r\n'],
    )
    assert r[1][0].startswith('503')


def test_proxy_illegal_command_aborts_421():
    async def go():
        return await _dialog_raw(
            ProxyEvents(), {'proxy_extension': True},
            [(b'PROXY GARBAGE\r\n', True)],
        )

    _, replies, closed = asyncio.run(go())
    assert replies[0][0].startswith('421')
    assert closed is True


def test_proxy_bad_params_abort_421():
    # TCP4 with an IPv6 address is an unsupported parameter -> 421 abort
    async def go():
        return await _dialog_raw(
            ProxyEvents(), {'proxy_extension': True},
            [(b'PROXY TCP4 ::1 ::2 1 2\r\n', True)],
        )

    _, replies, closed = asyncio.run(go())
    assert replies[0][0].startswith('421')
    assert closed is True


def test_proxy_twice_aborts_421():
    async def go():
        return await _dialog_raw(
            ProxyEvents(), {'proxy_extension': True},
            [(b'PROXY TCP4 1.2.3.4 5.6.7.8 1 2\r\n', False),
             (b'PROXY TCP4 9.9.9.9 8.8.8.8 3 4\r\n', True)],
        )

    _, replies, closed = asyncio.run(go())
    assert replies[0][0].startswith('421')
    assert closed is True


# --- graceful shutdown -------------------------------------------------------


async def _start_server(events, settings):
    """Start a server in the background on an ephemeral port; return (srv, port)."""
    srv = SmtpdServer(events, settings=settings)
    await srv.start([{'host': '127.0.0.1', 'port': 0}])
    port = srv._servers[0].sockets[0].getsockname()[1]
    return srv, port


def test_stop_stops_accepting_new_connections():
    async def go():
        srv, port = await _start_server(RecordingEvents(), {})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        banner = await _reply(reader)
        writer.close()
        await srv.stop(wait_seconds_before_close=0.2)
        refused = False
        try:
            await asyncio.open_connection('127.0.0.1', port)
        except OSError:
            refused = True
        return banner, refused, srv.stopped()

    banner, refused, stopped = asyncio.run(go())
    assert banner[0].startswith('220')
    assert refused is True
    assert stopped is True


def test_shutdown_ends_active_dialog_after_current_command():
    async def go():
        srv, port = await _start_server(RecordingEvents(), {})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await _reply(reader)
        writer.write(b'EHLO me\r\n')
        await writer.drain()
        await _reply(reader)
        # request a graceful shutdown; the next command is answered, then 221
        srv.shutdown()
        writer.write(b'NOOP\r\n')
        await writer.drain()
        noop = await _reply(reader)
        bye = await _reply(reader)
        writer.close()
        await srv.stop(wait_seconds_before_close=0.2)
        return noop, bye

    noop, bye = asyncio.run(go())
    assert noop[0] == '250 OK'
    assert bye[0].startswith('221')


def test_graceful_stop_cancels_idle_connection():
    async def go():
        srv, port = await _start_server(RecordingEvents(), {})
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await _reply(reader)
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


class AuthEvents(RecordingEvents):
    """Accepts one fixed credential pair; optionally returns an authz id."""

    def __init__(self, options=None):
        super().__init__(options)
        self.auth_calls = []
        self.authz_return = None
        self.ctx_auth = None

    def on_auth_event(self, ctx, authorization_id, authentication_id, authentication):
        self.auth_calls.append((authorization_id, authentication_id, authentication))
        if authentication_id == 'user' and authentication == 'secret':
            return self.authz_return
        from tokeo.core.smtpd.exc import Smtpd535Exception

        raise Smtpd535Exception

    def on_mail_from_event(self, ctx, mail_from):
        # capture the auth state as later events see it
        self.ctx_auth = (ctx.server.authenticated, ctx.server.authentication_id, ctx.server.authorization_id)
        return super().on_mail_from_event(ctx, mail_from)


def _plain(authzid, authcid, password):
    return base64.b64encode(f'{authzid}\x00{authcid}\x00{password}'.encode()).decode()


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def test_auth_forbidden_by_default_is_500():
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'AUTH PLAIN ' + _plain('', 'u', 'p').encode() + b'\r\n'])
    assert r[1][0].startswith('500')


def test_ehlo_advertises_auth_when_enabled():
    _, r = dialog(AuthEvents(), {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n'])
    assert any('AUTH LOGIN PLAIN' in ln for ln in r[0])


def test_auth_plain_inline_success_and_ctx():
    ev = AuthEvents()
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'EHLO me\r\n',
        b'AUTH PLAIN ' + _plain('', 'user', 'secret').encode() + b'\r\n',
        b'MAIL FROM:<a@x>\r\n',
    ])
    assert r[1][0] == '235 OK'
    assert r[2][0] == '250 OK'
    # event saw authzid/authcid/password; ctx carries the auth info afterwards
    assert ev.auth_calls == [('', 'user', 'secret')]
    authenticated, authentication_id, authorization_id = ev.ctx_auth
    assert authenticated and authentication_id == 'user'
    # empty authzid falls back to the authentication id
    assert authorization_id == 'user'


def test_auth_plain_wrong_credentials_535():
    ev = AuthEvents()
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'EHLO me\r\n',
        b'AUTH PLAIN ' + _plain('', 'user', 'WRONG').encode() + b'\r\n',
    ])
    assert r[1][0].startswith('535')


def test_auth_plain_prompt_form():
    ev = AuthEvents()
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'EHLO me\r\n',
        b'AUTH PLAIN\r\n',
        _plain('', 'user', 'secret').encode() + b'\r\n',
    ])
    # the bare AUTH PLAIN is answered with '334 ' (space included)
    assert r[1][0].rstrip() == '334'
    assert r[2][0] == '235 OK'


def test_auth_login_challenge_full():
    ev = AuthEvents()
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'EHLO me\r\n',
        b'AUTH LOGIN\r\n',
        _b64('user').encode() + b'\r\n',
        _b64('secret').encode() + b'\r\n',
    ])
    assert r[1][0] == '334 ' + _b64('Username:')
    assert r[2][0] == '334 ' + _b64('Password:')
    assert r[3][0] == '235 OK'
    assert ev.auth_calls == [('', 'user', 'secret')]


def test_auth_login_with_inline_user():
    ev = AuthEvents()
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'EHLO me\r\n',
        b'AUTH LOGIN ' + _b64('user').encode() + b'\r\n',
        _b64('secret').encode() + b'\r\n',
    ])
    assert r[1][0] == '334 ' + _b64('Password:')
    assert r[2][0] == '235 OK'


def test_auth_default_handler_denies_535():
    # the SmtpdEvents base rejects every authentication
    _, r = dialog(RecordingEvents(), {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'EHLO me\r\n',
        b'AUTH PLAIN ' + _plain('', 'any', 'thing').encode() + b'\r\n',
    ])
    assert r[1][0].startswith('535')


def test_auth_garbage_base64_500_and_recovers():
    ev = AuthEvents()
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'EHLO me\r\n',
        b'AUTH PLAIN\r\n',
        b'*\r\n',                    # abort: not valid credentials -> 500
        b'MAIL FROM:<a@x>\r\n',      # sequence was reset to RSET, MAIL works
    ])
    assert r[2][0].startswith('500')
    assert r[3][0] == '250 OK'


def test_auth_before_helo_503():
    _, r = dialog(AuthEvents(), {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'AUTH PLAIN ' + _plain('', 'user', 'secret').encode() + b'\r\n',
    ])
    assert r[0][0].startswith('503')


def test_auth_twice_503():
    ev = AuthEvents()
    _, r = dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'EHLO me\r\n',
        b'AUTH PLAIN ' + _plain('', 'user', 'secret').encode() + b'\r\n',
        b'AUTH PLAIN ' + _plain('', 'user', 'secret').encode() + b'\r\n',
    ])
    assert r[1][0] == '235 OK'
    assert r[2][0].startswith('503')


def test_auth_cram_md5_rejected_500():
    _, r = dialog(AuthEvents(), {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n', b'AUTH CRAM-MD5\r\n'])
    assert r[1][0].startswith('500')


def test_auth_required_gates_mail():
    ev = AuthEvents()
    _, r = dialog(ev, {'auth_mode': 'AUTH_REQUIRED'}, [
        b'EHLO me\r\n',
        b'MAIL FROM:<a@x>\r\n',      # not authenticated -> 530
        b'AUTH PLAIN ' + _plain('', 'user', 'secret').encode() + b'\r\n',
        b'MAIL FROM:<a@x>\r\n',      # authenticated -> 250
    ])
    assert r[1][0].startswith('530')
    assert r[2][0] == '235 OK'
    assert r[3][0] == '250 OK'


def test_auth_authzid_returned_by_handler():
    ev = AuthEvents()
    ev.authz_return = 'boss'
    dialog(ev, {'auth_mode': 'AUTH_OPTIONAL'}, [
        b'EHLO me\r\n',
        b'AUTH PLAIN ' + _plain('', 'user', 'secret').encode() + b'\r\n',
        b'MAIL FROM:<a@x>\r\n',
    ])
    # the handler's return value becomes the authorization id
    assert ev.ctx_auth[2] == 'boss'


# --- STARTTLS --------------------


def _tls_client_ctx():
    """A client SSL context that accepts the self-signed test certificate."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class TlsEvents(RecordingEvents):
    """Records whether the connection was encrypted at MAIL time."""

    def __init__(self, options=None):
        super().__init__(options)
        self.encrypted_at_mail = None

    def on_mail_from_event(self, ctx, mail_from):
        self.encrypted_at_mail = ctx.server.encrypted
        return super().on_mail_from_event(ctx, mail_from)


_TLS_SETTINGS = {'encrypt_mode': 'TLS_OPTIONAL', 'tls_cert_cn': 'localhost', 'tls_cert_san': ['127.0.0.1']}


def test_starttls_full_flow_delivers_over_tls():
    ev = TlsEvents()

    async def go():
        server, port, task = await _serve(ev, _TLS_SETTINGS)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await _reply(reader)
            writer.write(b'EHLO me\r\n')
            await writer.drain()
            ehlo1 = await _reply(reader)
            writer.write(b'STARTTLS\r\n')
            await writer.drain()
            tls_reply = await _reply(reader)
            await writer.start_tls(_tls_client_ctx())
            writer.write(b'EHLO secure\r\n')
            await writer.drain()
            ehlo2 = await _reply(reader)
            for cmd in (b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n'):
                writer.write(cmd)
                await writer.drain()
                await _reply(reader)
            writer.write(b'body over tls\r\n.\r\n')
            await writer.drain()
            final = await _reply(reader)
            writer.close()
            await asyncio.sleep(0.05)
            return ehlo1, tls_reply, ehlo2, final
        finally:
            await _stop(server, task)

    ehlo1, tls_reply, ehlo2, final = asyncio.run(go())
    assert any('STARTTLS' in ln for ln in ehlo1)
    assert tls_reply[0] == '220 Ready to start TLS'
    # already encrypted -> STARTTLS no longer advertised
    assert not any('STARTTLS' in ln for ln in ehlo2)
    assert final[0].startswith('250')
    assert ev.messages and ev.messages[0][0] == b'body over tls'
    assert ev.encrypted_at_mail


def test_starttls_forbidden_is_500():
    _, r = dialog(RecordingEvents(), {}, [b'EHLO me\r\n', b'STARTTLS\r\n'])
    assert r[1][0].startswith('500')


def test_starttls_before_helo_is_503():
    _, r = dialog(RecordingEvents(), _TLS_SETTINGS, [b'STARTTLS\r\n'])
    assert r[0][0].startswith('503')


def test_starttls_twice_already_encrypted_503():
    async def go():
        server, port, task = await _serve(RecordingEvents(), _TLS_SETTINGS)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await _reply(reader)
            writer.write(b'EHLO me\r\n')
            await writer.drain()
            await _reply(reader)
            writer.write(b'STARTTLS\r\n')
            await writer.drain()
            await _reply(reader)
            await writer.start_tls(_tls_client_ctx())
            writer.write(b'EHLO secure\r\n')
            await writer.drain()
            await _reply(reader)
            writer.write(b'STARTTLS\r\n')
            await writer.drain()
            second = await _reply(reader)
            writer.close()
            return second
        finally:
            await _stop(server, task)

    second = asyncio.run(go())
    assert second[0].startswith('503')


def test_tls_required_gates_mail_until_starttls():
    async def go():
        server, port, task = await _serve(RecordingEvents(), dict(_TLS_SETTINGS, encrypt_mode='TLS_REQUIRED'))
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await _reply(reader)
            writer.write(b'EHLO me\r\n')
            await writer.drain()
            await _reply(reader)
            writer.write(b'MAIL FROM:<a@x>\r\n')
            await writer.drain()
            before = await _reply(reader)
            writer.write(b'STARTTLS\r\n')
            await writer.drain()
            await _reply(reader)
            await writer.start_tls(_tls_client_ctx())
            writer.write(b'EHLO secure\r\n')
            await writer.drain()
            await _reply(reader)
            writer.write(b'MAIL FROM:<a@x>\r\n')
            await writer.drain()
            after = await _reply(reader)
            writer.close()
            return before, after
        finally:
            await _stop(server, task)

    before, after = asyncio.run(go())
    assert before[0].startswith('530')
    assert after[0] == '250 OK'


def test_starttls_flushes_injected_plaintext():
    # SECURITY: a command injected right after STARTTLS (before the handshake)
    # must be discarded, not run over the encrypted channel. With pipelining on,
    # the pipelining guard does not reject it, so only the flush protects here.
    # Inject QUIT: if it leaked, the server would close; with the flush the
    # connection stays alive and a fresh EHLO over TLS still succeeds.
    async def go():
        server, port, task = await _serve(RecordingEvents(), dict(_TLS_SETTINGS, pipelining_extension=True))
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await _reply(reader)
            writer.write(b'EHLO me\r\n')
            await writer.drain()
            await _reply(reader)
            # STARTTLS plus an injected QUIT in the same plaintext segment
            writer.write(b'STARTTLS\r\nQUIT\r\n')
            await writer.drain()
            tls_reply = await _reply(reader)
            await writer.start_tls(_tls_client_ctx())
            writer.write(b'EHLO secure\r\n')
            await writer.drain()
            ehlo = await _reply(reader)
            writer.close()
            return tls_reply, ehlo
        finally:
            await _stop(server, task)

    tls_reply, ehlo = asyncio.run(go())
    assert tls_reply[0] == '220 Ready to start TLS'
    # the injected QUIT was flushed: the session is alive and EHLO works
    assert ehlo[-1] == '250 OK'
