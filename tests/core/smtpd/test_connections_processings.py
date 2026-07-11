"""
Tests for the connections / processings readers on SmtpdServer.

Mirrors the ``connections`` / ``connections?`` / ``processings`` /
``processings?`` by driving a live server: the counters read zero when idle,
rise while clients stay connected or while a message is being processed, and
fall back once the clients close or the message is delivered.
"""

import asyncio

from tokeo.core.smtpd.server import SmtpdServer

from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_helpers import send_mail


async def _wait_until(pred, timeout=1.0):
    """Poll ```pred``` until true or the timeout elapses; return its final value."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and not pred():
        await asyncio.sleep(0.02)
    return pred()


def test_counters_are_zero_when_idle():
    srv = SmtpdServer(CaptureSmtpdEvents(), settings={})
    assert srv.connections == 0
    assert srv.has_connections() is False
    assert srv.processings == 0
    assert srv.has_processings() is False


def test_connections_counter_tracks_open_clients():
    async def scenario():
        srv = SmtpdServer(CaptureSmtpdEvents(), settings={})
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
        writers = []
        try:
            assert srv.connections == 0 and srv.has_connections() is False
            # each opened client raises the counter by one
            for expected in (1, 2, 3):
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                writers.append(writer)
                await reader.readline()  # consume the 220 greeting
                assert await _wait_until(lambda e=expected: srv.connections == e)
                assert srv.has_connections() is True
            # closing them one by one lowers it back to zero
            for closed, writer in enumerate(writers, start=1):
                writer.close()
                remaining = len(writers) - closed
                assert await _wait_until(lambda r=remaining: srv.connections == r)
            assert srv.has_connections() is False
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(scenario())


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
    events = _RecordingEvents()

    async def scenario():
        srv = SmtpdServer(events, settings={})
        events.server_ref = srv
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
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
            assert await _wait_until(lambda: srv.processings == 0)
            assert srv.has_processings() is False
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(scenario())
