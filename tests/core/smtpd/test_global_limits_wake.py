"""
Tests for the shared GlobalLimits wake-up (cross-service and cross-process).

The global processing cap makes admission WAIT. The wake event lives on
``GlobalLimits`` and is shared by every server bound to it, so a slot released
on any service wakes waiters on every service. When the cap may also be
released by another process (shared counters under pre_fork), the waiter
additionally re-checks the counters at a short interval.
"""

import asyncio

from tokeo.core.smtpd.server import SmtpdServer, GlobalLimits
from tokeo.core.smtpd.events import SmtpdEvents

from tests.core.smtpd.lib.smtpd_helpers import send_mail


class GatedEvents(SmtpdEvents):
    """Holds on_message_data_event until released (async, keeps the slot busy)."""

    def __init__(self, options=None):
        super().__init__(options)
        self.gate = asyncio.Event()
        self.delivered = []

    async def on_message_data_event(self, ctx):
        await self.gate.wait()
        self.delivered.append(bytes(ctx.message.data))


def test_servers_share_the_global_wake_event():
    gl = GlobalLimits(max_processings=1)
    a = SmtpdServer(GatedEvents(), global_limits=gl)
    b = SmtpdServer(GatedEvents(), global_limits=gl)
    assert a._slot_free is gl.slot_free
    assert b._slot_free is gl.slot_free
    # without global limits every server keeps its own event
    c = SmtpdServer(GatedEvents())
    assert c._slot_free is not gl.slot_free


def test_release_on_service_a_wakes_waiter_on_service_b():
    # one global processing slot; A occupies it (gated handler), B's client
    # must wait -- and MUST proceed once A releases (before the fix B starved)
    events_a, events_b = GatedEvents(), GatedEvents()
    gl = GlobalLimits(max_processings=1)

    async def scenario():
        srv_a = SmtpdServer(events_a, global_limits=gl)
        srv_b = SmtpdServer(events_b, global_limits=gl)
        await srv_a.start([{'host': '127.0.0.1', 'port': 0}])
        await srv_b.start([{'host': '127.0.0.1', 'port': 0}])
        port_a = srv_a._servers[0].sockets[0].getsockname()[1]
        port_b = srv_b._servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_event_loop()
        try:
            mail_a = loop.run_in_executor(
                None, lambda: send_mail(port_a, 'a@x.org', 'to@x.org', 'Subject: a\r\n\r\nA')
            )
            # wait until A really holds the one global slot
            deadline = loop.time() + 3.0
            while loop.time() < deadline and gl.active_processings < 1:
                await asyncio.sleep(0.02)
            assert gl.active_processings == 1

            mail_b = loop.run_in_executor(
                None, lambda: send_mail(port_b, 'b@x.org', 'to@x.org', 'Subject: b\r\n\r\nB')
            )
            await asyncio.sleep(0.3)          # B is now waiting for the slot
            assert events_b.delivered == []   # ... and did not get through yet

            events_a.gate.set()               # A finishes -> releases the slot
            events_b.gate.set()               # let B pass once admitted
            code_a, _ = await asyncio.wait_for(mail_a, 5.0)
            code_b, _ = await asyncio.wait_for(mail_b, 5.0)
            assert code_a == 250 and code_b == 250
            assert len(events_b.delivered) == 1
        finally:
            await srv_a.stop(wait_seconds_before_close=0.3)
            await srv_b.stop(wait_seconds_before_close=0.3)

    asyncio.run(scenario())


class PollingLimits(GlobalLimits):
    """The cross-process wait strategy the smtpd extension ships: counters may
    change from another process (no event reaches this loop), so re-check at a
    short interval while throttled."""

    async def slot_wait(self):
        try:
            await asyncio.wait_for(self.slot_free.wait(), 0.05)
        except asyncio.TimeoutError:
            pass


def test_external_counter_release_is_seen_via_slot_wait_override():
    # cross-process scenario: another process releases the global slot -- the
    # counters change but nobody sets this loop's event; a limits class with a
    # polling slot_wait (as the extension provides) must still admit the waiter
    events = GatedEvents()
    events.gate.set()  # deliver immediately once admitted
    gl = PollingLimits(max_processings=1)

    async def scenario():
        srv = SmtpdServer(events, global_limits=gl)
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_event_loop()
        try:
            gl.active_processings = 1  # as if another process holds the slot
            mail = loop.run_in_executor(
                None, lambda: send_mail(port, 'a@x.org', 'to@x.org', 'Subject: x\r\n\r\nhi')
            )
            await asyncio.sleep(0.3)
            assert events.delivered == []  # throttled by the "foreign" slot

            gl.active_processings = 0      # foreign process releases: NO event
            code, _ = await asyncio.wait_for(mail, 5.0)
            assert code == 250 and len(events.delivered) == 1
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(scenario())
