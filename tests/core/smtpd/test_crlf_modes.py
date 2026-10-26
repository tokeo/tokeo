"""
Tests for the crlf_mode handling on the raw wire.

``CRLF_LEAVE`` must deliver the message bytes exactly as received (only the
protocol-mandated de-dot-stuffing and the final chomp apply), ``CRLF_ENSURE``
normalizes every line to CRLF and strips stray CR/LF octets, ``CRLF_STRICT``
rejects lines that do not end with CRLF. A rest without LF that the client
leaves at its close is dropped in every mode. smtplib normalizes line endings on
send, so these tests speak to the socket directly.
"""

import asyncio

import pytest

from tokeo.core.smtpd.events import SmtpdEvents
from tests.core.smtpd.lib.smtpd_tests_helper import RecordingEvents, run_wire, start_server, stop_server, read_reply, send_command


class WireCapture(SmtpdEvents):
    """Records message facts at on_message_data_event time."""

    def __init__(self, options=None):
        super().__init__(options)
        self.data = None
        self.crlf = None
        self.bytesize = None
        self.file = None

    def on_message_data_event(self, ctx):
        self.data = bytes(ctx.message.data)
        self.crlf = ctx.message.crlf
        self.bytesize = ctx.message.bytesize
        spooler = ctx.message.spooler
        if spooler is not None and spooler.path:
            with open(spooler.path, 'rb') as f:
                self.file = f.read()


#: mixed endings, interior CR, LF-only separator, dot-stuffing, CR before CRLF
NASTY = b'Subject: t\n' b'X: a\rb\r\n' b'\n' b'A\r\n' b'B\n' b'..C\r\n' b'D\r\r\n'


def test_leave_preserves_wire_bytes():
    """CRLF_LEAVE delivers every byte as received, de-dotted and chomped."""
    events = WireCapture()
    reply = run_wire(events, {'crlf_mode': 'CRLF_LEAVE'}, NASTY)
    assert reply == b'250'
    # de-dot-stuffed and final chomp only; every other byte as received
    assert events.data == b'Subject: t\nX: a\rb\r\n\nA\r\nB\n.C\r\nD\r'
    # ctx.message.crlf records the line break of the last DATA line
    assert events.crlf == b'\r\n'


def test_ensure_normalizes_lines():
    """CRLF_ENSURE ends every line with CRLF and drops stray CR and LF."""
    events = WireCapture()
    reply = run_wire(events, {'crlf_mode': 'CRLF_ENSURE'}, NASTY)
    assert reply == b'250'
    # every line CRLF-terminated, stray CR/LF octets removed
    assert events.data == b'Subject: t\r\nX: ab\r\n\r\nA\r\nB\r\n.C\r\nD'


def test_strict_rejects_bare_lf():
    """CRLF_STRICT answers a line with bare LF by 500."""
    events = WireCapture()
    reply = run_wire(events, {'crlf_mode': 'CRLF_STRICT'}, b'Subject: t\n')
    assert reply == b'500'
    # the offending line was rejected and never reached the message (the
    # session stays in DATA mode, so the closing dot completes empty)
    assert not events.data


class ClosingEvents(RecordingEvents):
    """Records the events and the log lines."""

    def __init__(self, options=None):
        super().__init__(options)
        self.lines = []

    def on_logging_event(self, ctx, severity, msg, err=None):
        self.lines.append(msg)


def _close_after(events, settings, commands, rest):
    """Send the commands, then the rest and close; return what follows."""

    async def go():
        server, port, task = await start_server(events, settings)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            for command in commands:
                await send_command(reader, writer, command)
            writer.write(rest)
            writer.write_eof()
            after = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            # give the server the time to reach on_disconnect_event
            await asyncio.sleep(0.1)
            return after
        finally:
            await stop_server(server, task)

    return asyncio.run(go())


ENVELOPE = [b'HELO a\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n']


@pytest.mark.parametrize('crlf_mode', ['CRLF_ENSURE', 'CRLF_LEAVE', 'CRLF_STRICT'])
def test_dot_without_crlf_at_close_delivers_nothing(crlf_mode):
    """A final dot cut by the close of the client neither ends nor delivers DATA."""
    events = ClosingEvents()
    after = _close_after(events, {'crlf_mode': crlf_mode}, ENVELOPE, b'Subject: t\r\n\r\nline\r\n.')
    assert after == b''
    assert events.messages == []
    assert 'disconnect' in events.fired


@pytest.mark.parametrize('crlf_mode', ['CRLF_ENSURE', 'CRLF_LEAVE', 'CRLF_STRICT'])
def test_command_without_lf_at_close_is_not_run(crlf_mode):
    """A command cut by the close of the client is not run, nothing is replied."""
    events = ClosingEvents()
    after = _close_after(events, {'crlf_mode': crlf_mode}, [b'HELO a\r\n'], b'MAIL FROM:<a@')
    assert after == b''
    assert 'mail' not in events.fired
    after = _close_after(ClosingEvents(), {'crlf_mode': crlf_mode}, [b'HELO a\r\n'], b'QUIT')
    # no 221 for a QUIT without CRLF
    assert after == b''


def test_close_without_quit_gets_no_reply_and_is_logged():
    """The close of the client without QUIT ends the dialog without a reply."""
    events = ClosingEvents()
    after = _close_after(events, {}, [b'HELO a\r\n'], b'')
    assert after == b''
    assert 'Connection lost due abort by client! (EOFError)' in events.lines
    assert not any(line.startswith('>>> 421') for line in events.lines)
