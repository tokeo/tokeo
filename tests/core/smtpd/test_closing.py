"""
Tests for closing the connection by the server.

A ```SmtpdCloseException``` answers by its code and closes, raised within
DATA only after the end of DATA; a ```SmtpdAbortException``` closes at once,
also within DATA. The internal signals abort at once: a line over
```io_buffer_max_size``` by 554, a session over ```io_session_timeout``` and
a client that does not take its replies within ```io_cmd_timeout``` by 421.
A reset by the client is counted and logged by ERROR without a reply. Every
error of the connection is in ```ctx.server.errors``` when
```on_disconnect_event``` runs. These tests speak to the socket directly.
"""

import time
import socket
import struct
import asyncio

import pytest

from tokeo.core.smtpd.server import SmtpdServer
from tokeo.core.smtpd.logger import Severity
from tokeo.core.smtpd.exc import (
    Smtpd421CloseException,
    Smtpd521CloseException,
    Smtpd554CloseException,
    Smtpd554Exception,
    Smtpd421AbortException,
    Smtpd554AbortException,
    SmtpdIOCmdTimeoutException,
    SmtpdIOSessionTimeoutException,
    SmtpdIOBufferOverrunException,
)
from tests.utils import wait_until_async
from tests.core.smtpd.lib.smtpd_tests_helper import RecordingEvents, start_server, stop_server, read_reply, send_command

ENVELOPE = [b'HELO a\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n']


class ClosingEvents(RecordingEvents):
    """Records the errors of the connection and raises in the given event."""

    def __init__(self, raise_in=None, exc=None, options=None):
        super().__init__(options)
        self.raise_in = raise_in
        self.exc = exc
        self.errors = None
        self.exception_cnt = None
        self.logged_messages = []

    def on_connect_event(self, ctx):
        super().on_connect_event(ctx)
        if self.raise_in == 'connect':
            raise self.exc

    def on_mail_from_event(self, ctx, mail_from):
        if self.raise_in == 'mail':
            raise self.exc
        return super().on_mail_from_event(ctx, mail_from)

    def on_message_data_receiving_event(self, ctx):
        super().on_message_data_receiving_event(ctx)
        if self.raise_in == 'receiving':
            raise self.exc

    def on_logging_event(self, ctx, severity, msg, err=None):
        self.logged_messages.append((severity, msg))

    def on_disconnect_event(self, ctx):
        super().on_disconnect_event(ctx)
        self.errors = [type(e) for e in ctx.server.errors]
        self.exception_cnt = ctx.server.exception_cnt


def _run(events, options, run_client):
    """Run the client against a server and wait for on_disconnect_event."""

    async def go():
        server, port, task = await start_server(events, options)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            result = await run_client(reader, writer)
            writer.close()
            assert await wait_until_async(lambda: 'disconnect' in events.fired_events)
            return result
        finally:
            await stop_server(server, task)

    return asyncio.run(go())


async def _send_envelope(reader, writer):
    """Read the greeting and send the envelope up to DATA."""
    await read_reply(reader)
    for command in ENVELOPE:
        await send_command(reader, writer, command)


# close exceptions


@pytest.mark.parametrize(
    'exc, reply',
    [(Smtpd554CloseException, b'554 '), (Smtpd521CloseException, b'521 '), (Smtpd554Exception, b'421 ')],
    ids=['close_554', 'close_521', 'plain_554'],
)
def test_close_exception_in_connect_answers_its_code(exc, reply):
    """In on_connect_event a close exception answers by its code, others by 421."""
    events = ClosingEvents('connect', exc)

    async def run_client(reader, writer):
        return await asyncio.wait_for(reader.read(), 5)

    after = _run(events, {}, run_client)
    assert after.startswith(reply)
    assert after.count(b'\r\n') == 1
    assert events.errors == [exc]


def test_close_exception_in_mail_from_answers_521_and_closes():
    """Smtpd521CloseException in MAIL answers 521 and closes, NOOP does not run."""
    events = ClosingEvents('mail', Smtpd521CloseException)

    async def run_client(reader, writer):
        await read_reply(reader)
        await send_command(reader, writer, b'HELO a\r\n')
        writer.write(b'MAIL FROM:<a@x>\r\nNOOP\r\n')
        await writer.drain()
        return await asyncio.wait_for(reader.read(), 5)

    after = _run(events, {'pipelining_extension': True}, run_client)
    assert after == b'521 Service does not accept mail\r\n'
    assert events.errors == [Smtpd521CloseException]


def test_close_exception_within_data_answers_after_its_end():
    """Smtpd421CloseException within DATA answers 421 only after its end."""
    events = ClosingEvents('receiving', Smtpd421CloseException)

    async def run_client(reader, writer):
        await _send_envelope(reader, writer)
        writer.write(b'Subject: t\r\n\r\nA\r\n')
        await writer.drain()
        # no reply before the end of DATA
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1), 0.5)
        writer.write(b'B\r\n.\r\n')
        await writer.drain()
        return await asyncio.wait_for(reader.read(), 5)

    after = _run(events, {}, run_client)
    assert after.startswith(b'421 ')
    assert after.count(b'\r\n') == 1
    assert events.messages == []
    assert events.errors == [Smtpd421CloseException]


# abort exceptions


@pytest.mark.parametrize('exc, reply', [(Smtpd421AbortException, b'421 '), (Smtpd554AbortException, b'554 ')], ids=['421', '554'])
def test_abort_exception_within_data_answers_at_once(exc, reply):
    """An abort exception within DATA answers at once and closes."""
    events = ClosingEvents('receiving', exc)

    async def run_client(reader, writer):
        await _send_envelope(reader, writer)
        # DATA does not end, the reply comes without it
        writer.write(b'Subject: t\r\n\r\nA\r\n')
        await writer.drain()
        return await asyncio.wait_for(reader.read(), 5)

    after = _run(events, {}, run_client)
    assert after.startswith(reply)
    assert after.count(b'\r\n') == 1
    assert events.messages == []
    assert events.errors == [exc]


# io_buffer_max_size


@pytest.mark.parametrize('in_data', [False, True], ids=['command', 'data'])
def test_line_over_io_buffer_max_size_answers_554_and_closes(in_data):
    """A command or DATA line over io_buffer_max_size answers 554 and closes."""
    events = ClosingEvents()

    async def run_client(reader, writer):
        if in_data:
            await _send_envelope(reader, writer)
        else:
            await read_reply(reader)
        writer.write(b'x' * 300 + b'\r\n')
        await writer.drain()
        return await asyncio.wait_for(reader.read(), 5)

    after = _run(events, {'io_buffer_max_size': 100}, run_client)
    assert after == b'554 Transaction failed\r\n'
    assert events.errors == [SmtpdIOBufferOverrunException]


# io_session_timeout


def test_session_timeout_ends_a_noop_loop():
    """NOOP without end gets 421 after io_session_timeout of 1 second."""
    events = ClosingEvents()

    async def run_client(reader, writer):
        await read_reply(reader)
        start_time = time.monotonic()
        replies = []
        while not replies or replies[-1] == '250':
            await asyncio.sleep(0.2)
            reply = await asyncio.wait_for(send_command(reader, writer, b'NOOP\r\n'), 5)
            replies.append(reply[0][:3])
        return replies, time.monotonic() - start_time

    replies, elapsed_seconds = _run(events, {'io_session_timeout': 1}, run_client)
    assert replies[-1] == '421'
    assert len(replies) > 2
    assert elapsed_seconds < 2
    assert events.errors == [SmtpdIOSessionTimeoutException]


def test_session_timeout_ends_discarded_data():
    """DATA over max_data_bytes is discarded, 421 after io_session_timeout 1 s."""
    events = ClosingEvents()

    async def run_client(reader, writer):
        await _send_envelope(reader, writer)

        async def feed():
            # a stream of DATA lines without end
            while True:
                writer.write(b'X' * 70 + b'\r\n')
                await writer.drain()
                await asyncio.sleep(0.05)

        feed_task = asyncio.create_task(feed())
        try:
            return await asyncio.wait_for(read_reply(reader), 5)
        finally:
            feed_task.cancel()

    reply = _run(events, {'io_session_timeout': 1, 'max_data_bytes': 100}, run_client)
    assert reply[0].startswith('421')
    assert events.messages == []
    assert events.errors == [SmtpdIOSessionTimeoutException]


def test_session_timeout_is_not_renewed_by_a_message():
    """A message accepted after 0.7 s does not renew io_session_timeout of 1 s."""
    events = ClosingEvents()

    async def run_client(reader, writer):
        await read_reply(reader)
        start_time = time.monotonic()
        await asyncio.sleep(0.7)
        for command in ENVELOPE:
            await send_command(reader, writer, command)
        accepted_reply = await send_command(reader, writer, b'Subject: t\r\n\r\nA\r\n.\r\n')
        # the server closes on its own at the end of the session
        reply = await asyncio.wait_for(read_reply(reader), 5)
        return accepted_reply, reply, time.monotonic() - start_time

    accepted_reply, reply, elapsed_seconds = _run(events, {'io_session_timeout': 1}, run_client)
    assert accepted_reply[0].startswith('250')
    assert reply[0].startswith('421')
    assert elapsed_seconds < 1.5
    assert len(events.messages) == 1


# io_cmd_timeout for the replies


def test_client_that_does_not_read_is_dropped_after_io_cmd_timeout():
    """An unread reply of 1 MB ends the connection after io_cmd_timeout of 1 s."""

    class HugeReplyEvents(ClosingEvents):

        def on_connect_event(self, ctx):
            super().on_connect_event(ctx)
            ctx.server.helo_response = 'x' * 1_000_000

    events = HugeReplyEvents()

    async def go():
        srv = SmtpdServer(events, options={'io_cmd_timeout': 1})

        async def on_connection(reader, writer):
            # a small send buffer of the server fills up quickly
            writer.get_extra_info('socket').setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            await srv._on_connection(reader, writer)

        server = await asyncio.start_server(on_connection, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.connect(('127.0.0.1', port))
        sock.setblocking(False)
        reader, writer = await asyncio.open_connection(sock=sock)
        try:
            start_time = time.monotonic()
            # the client sends EHLO and reads nothing
            writer.write(b'EHLO a\r\n')
            await writer.drain()
            assert await wait_until_async(lambda: 'disconnect' in events.fired_events)
            return time.monotonic() - start_time
        finally:
            writer.transport.abort()
            server.close()

    elapsed_seconds = asyncio.run(go())
    assert elapsed_seconds < 3
    assert events.errors == [SmtpdIOCmdTimeoutException]


# reset by the client


def test_reset_by_client_is_counted_and_logged_as_error():
    """An RST after HELO counts one exception, logs ERROR and sends no 421."""
    events = ClosingEvents()

    async def run_client(reader, writer):
        await read_reply(reader)
        await send_command(reader, writer, b'HELO a\r\n')
        # SO_LINGER with 0 seconds closes by RST instead of FIN
        writer.get_extra_info('socket').setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        writer.transport.abort()

    _run(events, {}, run_client)
    assert events.exception_cnt == 1
    assert events.errors == [ConnectionResetError]
    assert (Severity.ERROR, 'Connection lost due reset by client!') in events.logged_messages
    assert not any(msg.startswith("Can't send") for _, msg in events.logged_messages)
