"""
Tests for the crlf_mode handling on the raw wire.

``CRLF_LEAVE`` must deliver the message bytes exactly as received (only the
protocol-mandated de-dot-stuffing and the final chomp apply), ``CRLF_ENSURE``
normalizes every line to CRLF and strips stray CR/LF octets, ``CRLF_STRICT``
rejects lines that do not end with CRLF. smtplib normalizes line endings on
send, so these tests speak to the socket directly.
"""

from tokeo.core.smtpd.events import SmtpdEvents
from tests.core.smtpd.lib.smtpd_tests_helper import run_wire


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
    events = WireCapture()
    reply = run_wire(events, {'crlf_mode': 'CRLF_LEAVE'}, NASTY)
    assert reply == b'250'
    # de-dot-stuffed and final chomp only; every other byte as received
    assert events.data == b'Subject: t\nX: a\rb\r\n\nA\r\nB\n.C\r\nD\r'
    # ctx.message.crlf records the line break of the last DATA line
    assert events.crlf == b'\r\n'


def test_ensure_normalizes_lines():
    events = WireCapture()
    reply = run_wire(events, {'crlf_mode': 'CRLF_ENSURE'}, NASTY)
    assert reply == b'250'
    # every line CRLF-terminated, stray CR/LF octets removed
    assert events.data == b'Subject: t\r\nX: ab\r\n\r\nA\r\nB\r\n.C\r\nD'


def test_strict_rejects_bare_lf():
    events = WireCapture()
    reply = run_wire(events, {'crlf_mode': 'CRLF_STRICT'}, b'Subject: t\n')
    assert reply == b'500'
    # the offending line was rejected and never reached the message (the
    # session stays in DATA mode, so the closing dot completes empty)
    assert not events.data
