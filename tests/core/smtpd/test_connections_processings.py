"""
Tests for the connections / processings readers on SmtpdServer.

Mirrors the ```connections``` / ```connections?``` / ```processings``` /
```processings?``` by driving a live server: the counters read zero when idle,
rise while clients stay connected or while a message is being processed, and
fall back once the clients close or the message is delivered.
"""

import asyncio

from tokeo.core.smtpd.server import SmtpdServer

from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents
from tests.utils import wait_until_async
from tests.core.smtpd.lib.smtpd_tests_helper import send_mail, start_smtpd


def test_counters_are_zero_when_idle():
    """A new server counts 0 connections and 0 processings."""
    srv = SmtpdServer(CaptureSmtpdEvents(), settings={})
    assert srv.connections == 0
    assert srv.has_connections() is False
    assert srv.processings == 0
    assert srv.has_processings() is False


def test_connections_counter_tracks_open_clients():
    """connections rises with each open client and falls when they close."""

    async def go():
        srv, port = await start_smtpd(CaptureSmtpdEvents(), settings={})
        writers = []
        try:
            assert srv.connections == 0 and srv.has_connections() is False
            # each opened client raises the counter by one
            for expected in (1, 2, 3):
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                writers.append(writer)
                await reader.readline()  # consume the 220 greeting
                assert await wait_until_async(lambda e=expected: srv.connections == e)
                assert srv.has_connections() is True
            # closing them one by one lowers it back to zero
            for closed, writer in enumerate(writers, start=1):
                writer.close()
                remaining = len(writers) - closed
                assert await wait_until_async(lambda r=remaining: srv.connections == r)
            assert srv.has_connections() is False
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(go())


class _RecordingEvents(CaptureSmtpdEvents):
    """Captures the server's processings count observed during the data event."""

    def __init__(self, options=None):
        super().__init__(options)
        self.server_ref = None
        self.processings_during = None
        self.has_processings_during = None

    def on_message_data_event(self, ctx):
        super().on_message_data_event(ctx)
        self.processings_during = self.server_ref.processings
        self.has_processings_during = self.server_ref.has_processings()


def test_processings_counter_tracks_message_processing():
    """processings is at least 1 during the data event and 0 after."""
    events = _RecordingEvents()

    async def go():
        srv, port = await start_smtpd(events, settings={})
        events.server_ref = srv
        loop = asyncio.get_event_loop()
        try:
            assert srv.processings == 0 and srv.has_processings() is False
            await loop.run_in_executor(
                None,
                lambda: send_mail(port, 'from@example.org', 'to@example.org', 'Subject: x\r\n\r\nhi'),
            )
            # the message was counted as in-processing while the data event ran
            assert events.processings_during >= 1
            assert events.has_processings_during is True
            # and released again after delivery
            assert await wait_until_async(lambda: srv.processings == 0)
            assert srv.has_processings() is False
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(go())
