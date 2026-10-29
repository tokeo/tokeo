"""
The per-connection session id: created once in ```serve_client``` as
```ctx.id``` (write-once), sized by the keyword-only ```session_id_bytes```
of the server, never by a service setting.
"""

import re
import asyncio
import smtplib

import pytest

from tokeo.core.smtpd.context import SmtpdContext
from tokeo.core.smtpd.server import SmtpdServer

from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_tests_helper import start_smtpd


class IdCaptureEvents(CaptureSmtpdEvents):
    """Records ctx.id at connect and again at every MAIL FROM."""

    def __init__(self, options=None):
        super().__init__(options=options)
        self.seen_ids = []

    def on_connect_event(self, ctx):
        self.seen_ids.append(('connect', ctx.id))

    def on_mail_from_event(self, ctx, mail_from_data):
        self.seen_ids.append(('mail', ctx.id))


def _run(events, client_calls, **server_kwargs):
    async def go():
        srv, port = await start_smtpd(events, **server_kwargs)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, client_calls, port)
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(go())


def _session(port):
    client = smtplib.SMTP('127.0.0.1', port, timeout=10)
    client.ehlo('probe')
    client.mail('a@local')
    client.rset()
    client.mail('b@local')
    client.quit()


def test_ctx_id_is_write_once():
    """ctx.id is set once, a second assignment raises AttributeError."""
    ctx = SmtpdContext(id='abc123')
    assert ctx.id == 'abc123'
    with pytest.raises(AttributeError):
        ctx.id = 'other'
    # the empty default allows exactly one late assignment
    ctx = SmtpdContext()
    ctx.id = 'once'
    with pytest.raises(AttributeError):
        ctx.id = 'twice'


def test_session_id_format_uniqueness_and_persistence():
    """Each connection gets its own 16 hex id, kept across RSET."""
    events = IdCaptureEvents()
    _run(events, lambda port: (_session(port), _session(port)))
    connects = [sid for kind, sid in events.seen_ids if kind == 'connect']
    mails = [sid for kind, sid in events.seen_ids if kind == 'mail']
    # two connections -> two distinct ids, default 8 bytes -> 16 hex chars
    assert len(connects) == 2 and connects[0] != connects[1]
    for sid in connects:
        assert re.fullmatch(r'[0-9a-f]{16}', sid)
    # the id persists across RSET within one connection (2 MAILs each)
    assert mails == [connects[0], connects[0], connects[1], connects[1]]


def test_session_id_bytes_is_keyword_only_not_a_service_setting():
    """session_id_bytes sizes the id as keyword, the setting is ignored."""
    events = IdCaptureEvents()
    # the keyword sizes the id; the same key in settings must stay ignored
    _run(events, _session, settings={'session_id_bytes': 12}, session_id_bytes=4)
    assert re.fullmatch(r'[0-9a-f]{8}', events.seen_ids[0][1])
    srv = SmtpdServer(IdCaptureEvents(), settings={'session_id_bytes': 12})
    assert srv.session_id_bytes == 8
