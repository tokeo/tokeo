"""
Tests for the precomputed event dispatch (SmtpdServer.emit_events).

The table is built once per handler class and keyed by ```SmtpdEvent```:
overridden events get an entry with their resolved execution kind (sync /
async / @threaded); base no-ops get none, so call sites skip ```_emit```
entirely; events whose base carries behaviour (```ON_AUTH_EVENT```'s default
deny) always stay dispatchable.
"""

import asyncio

import pytest

from tokeo.core.smtpd.server import SmtpdServer
from tokeo.core.smtpd.events import (
    SmtpdEvents,
    SmtpdEvent,
    SMTPD_EVENT_NAMES,
    threaded,
)
from tokeo.core.smtpd.exc import Smtpd535Exception

from tests.core.smtpd.lib.smtpd_tests_helper import send_mail, start_smtpd


class BareEvents(SmtpdEvents):
    """No overrides at all."""


class MixedEvents(SmtpdEvents):
    """One sync, one async and one @threaded override."""

    def on_helo_event(self, ctx, helo_data):
        pass

    async def on_mail_from_event(self, ctx, mail_from_data):
        pass

    @threaded
    def on_message_data_event(self, ctx):
        pass


def _ctx():
    from tokeo.core.smtpd.context import SmtpdContext

    ctx = SmtpdContext()
    ctx.server.remote_ip = '127.0.0.1'
    ctx.server.remote_port = '12345'
    return ctx


def test_event_names_map_covers_every_event_and_existing_methods():
    """Every SmtpdEvent maps to an existing method of SmtpdEvents."""
    assert set(SMTPD_EVENT_NAMES) == set(SmtpdEvent)
    for ev, name in SMTPD_EVENT_NAMES.items():
        assert callable(getattr(SmtpdEvents, name)), f'{ev}: {name}'


def test_base_noops_have_no_entry():
    """Without overrides only events with a behavioural default are dispatched."""
    dispatch = SmtpdServer(BareEvents()).emit_events
    # only the behavioural base events stay dispatchable without an override
    assert set(dispatch) == {
        SmtpdEvent.ON_AUTH_EVENT,
        SmtpdEvent.ON_LOGGING_EVENT,
        SmtpdEvent.ON_CONNECT_EVENT,
        SmtpdEvent.ON_DISCONNECT_EVENT,
    }


def test_overridden_events_get_entries_with_resolved_kind():
    """Overrides get entries as sync, async or thread, bound to the handler."""
    srv = SmtpdServer(MixedEvents())
    dispatch = srv.emit_events
    assert dispatch[SmtpdEvent.ON_HELO_EVENT][0] == srv._EMIT_SYNC
    assert dispatch[SmtpdEvent.ON_MAIL_FROM_EVENT][0] == srv._EMIT_ASYNC
    assert dispatch[SmtpdEvent.ON_MESSAGE_DATA_EVENT][0] == srv._EMIT_THREAD
    # entries hold the bound methods of exactly this handler instance
    assert dispatch[SmtpdEvent.ON_HELO_EVENT][1].__self__ is srv.events_handler
    # non-overridden no-ops still have no entry
    assert SmtpdEvent.ON_MESSAGE_DATA_RECEIVING_EVENT not in dispatch


def test_auth_default_deny_stays_dispatchable():
    """Without an override ON_AUTH_EVENT still denies by 535."""
    srv = SmtpdServer(BareEvents())
    assert SmtpdEvent.ON_AUTH_EVENT in srv.emit_events

    async def go():
        with pytest.raises(Smtpd535Exception):
            await srv._emit(SmtpdEvent.ON_AUTH_EVENT, _ctx(), '', 'user', 'secret')

    asyncio.run(go())


def test_emit_returns_none_for_skipped_event():
    """_emit of an event without entry returns None."""
    srv = SmtpdServer(BareEvents())

    async def go():
        assert await srv._emit(SmtpdEvent.ON_HELO_EVENT, _ctx(), 'x') is None

    asyncio.run(go())


def test_mail_flows_end_to_end_with_bare_handler():
    """A handler without overrides still delivers a mail with 250."""

    async def go():
        srv, port = await start_smtpd(BareEvents(), settings={})
        loop = asyncio.get_event_loop()
        try:
            code, _ = await loop.run_in_executor(
                None,
                lambda: send_mail(port, 'from@example.org', 'to@example.org', 'Subject: x\r\n\r\nhello'),
            )
            assert code == 250
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(go())
