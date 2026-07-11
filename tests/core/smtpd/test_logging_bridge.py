"""
Tests for the logging bridge (SmtpdServer._log_bridge) and the handler's
bound ``self.log``.

``on_logging_event`` is free to be a plain ``def``, an ``async def`` or a
``@threaded def``; the bridge hands the ``_emit`` coroutine to the event loop,
so the execution model stays with the precomputed dispatcher. Every event, in
any of its three forms, logs the same way: ``self.log.warn(...)`` -- never
awaited.
"""

import asyncio
import time

import pytest

from tokeo.core.smtpd.server import SmtpdServer
from tokeo.core.smtpd.events import SmtpdEvents, threaded
from tokeo.core.smtpd.logger import Severity

from tests.core.smtpd.lib.smtpd_helpers import send_mail


class SyncLogging(SmtpdEvents):
    """on_logging_event as plain sync def."""

    def __init__(self, options=None):
        super().__init__(options)
        self.captured = []

    def on_logging_event(self, ctx, severity, msg, err=None):
        self.captured.append((severity, msg))


class AsyncLogging(SmtpdEvents):
    """on_logging_event as async def -- allowed again (no guard)."""

    def __init__(self, options=None):
        super().__init__(options)
        self.captured = []

    async def on_logging_event(self, ctx, severity, msg, err=None):
        self.captured.append((severity, msg))


class ThreadedLogging(SmtpdEvents):
    """on_logging_event as @threaded def."""

    def __init__(self, options=None):
        super().__init__(options)
        self.captured = []

    @threaded
    def on_logging_event(self, ctx, severity, msg, err=None):
        self.captured.append((severity, msg))


@pytest.mark.parametrize('handler_cls', [SyncLogging, AsyncLogging, ThreadedLogging])
def test_logger_reaches_every_logging_event_form(handler_cls):
    # smtpd.logger without a running loop: the bridge runs the coroutine to
    # completion in place, whatever the declared form of on_logging_event
    events = handler_cls()
    smtpd = SmtpdServer(events)
    smtpd.logger.warn('bridge me')
    assert events.captured == [(Severity.WARN, 'bridge me')]


def test_handler_log_is_bound_to_the_server_logger():
    events = SyncLogging()
    smtpd = SmtpdServer(events)
    # the handler's self.log is the very server logger
    assert events.log is smtpd.logger
    events.log.info('via handler')
    assert events.captured == [(Severity.INFO, 'via handler')]


def test_handler_log_is_none_before_binding():
    # contract: self.log stays None until a server binds its logger at
    # construction -- logging from an unbound handler fails loudly
    events = SyncLogging()
    assert events.log is None


def test_self_log_from_sync_and_async_events_with_async_logging():
    # live dialog: a sync and an async event both call self.log while
    # on_logging_event itself is async -> scheduled on the loop, captured
    class Handler(AsyncLogging):
        def on_mail_from_event(self, ctx, mail_from_data):
            self.log.info('mail_from (sync event)')

        async def on_rcpt_to_event(self, ctx, rcpt_to_data):
            self.log.info('rcpt_to (async event)')

    events = Handler()

    async def scenario():
        srv = SmtpdServer(events, settings={})
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_event_loop()
        try:
            code, _ = await loop.run_in_executor(
                None,
                lambda: send_mail(port, 'from@example.org', 'to@example.org', 'Subject: x\r\n\r\nhi'),
            )
            assert code == 250
            await asyncio.sleep(0.1)  # let fire-and-forget tasks run
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(scenario())
    messages = [m for _, m in events.captured]
    assert 'mail_from (sync event)' in messages
    assert 'rcpt_to (async event)' in messages


def test_self_log_from_threaded_event_reaches_loop_threadsafe():
    # a @threaded event runs on a worker thread (no running loop there); the
    # bridge must schedule thread-safe onto the server loop
    class Handler(AsyncLogging):
        @threaded
        def on_message_data_event(self, ctx):
            self.log.info('data (threaded event)')
            time.sleep(0.01)

    events = Handler()

    async def scenario():
        srv = SmtpdServer(events, settings={})
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_event_loop()
        try:
            code, _ = await loop.run_in_executor(
                None,
                lambda: send_mail(port, 'from@example.org', 'to@example.org', 'Subject: x\r\n\r\nhi'),
            )
            assert code == 250
            await asyncio.sleep(0.2)
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(scenario())
    assert ('data (threaded event)' in [m for _, m in events.captured])


def test_base_auth_deny_logs_via_bound_self_log():
    # the base on_auth_event routes its deny log through self.log, so it works
    # for every on_logging_event form; the 535 still raises
    from tokeo.core.smtpd.exc import Smtpd535Exception
    from tokeo.core.smtpd.context import SmtpdContext
    from tokeo.core.smtpd.events import SmtpdEvent

    events = AsyncLogging()
    smtpd = SmtpdServer(events)
    ctx = SmtpdContext()
    ctx.server.remote_ip = '127.0.0.1'
    ctx.server.remote_port = '12345'

    async def call():
        with pytest.raises(Smtpd535Exception):
            await smtpd._emit(SmtpdEvent.ON_AUTH_EVENT, ctx, '', 'user', 'secret')
        await asyncio.sleep(0.05)  # let the scheduled deny log run

    asyncio.run(call())
    assert any('Deny access' in m for _, m in events.captured)
