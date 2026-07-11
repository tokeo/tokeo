"""
Ported from midi-smtp-server test/integration/ports_and_connections_test.rb.

With max_connections=2 and max_processings=1: one connection is served, a second
waits (before its welcome) until the first frees the slot, and a third is
refused with the 421 abort message.
"""

import asyncio

from tokeo.core.smtpd.server import SmtpdServer
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents

MSG_WELCOME = '220 127.0.0.1 says welcome!\r\n'
MSG_ABORT = '421 Service too busy or not available, closing transmission channel\r\n'

SETTINGS = {'max_processings': 1, 'max_connections': 2, 'do_dns_reverse_lookup': False}


async def _serve():
    srv = SmtpdServer(CaptureSmtpdEvents(), settings=SETTINGS)
    await srv.start([{'host': '127.0.0.1', 'port': 0}])
    return srv, srv._servers[0].sockets[0].getsockname()[1]


async def _welcome(reader):
    return (await reader.readuntil(b'\n')).decode()


def test_010_tcp_1_connect():
    async def go():
        srv, port = await _serve()
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            banner = await _welcome(reader)
            writer.close()
            return banner
        finally:
            await srv.stop(wait_seconds_before_close=0.2)

    assert asyncio.run(go()) == MSG_WELCOME


def test_020_tcp_2_simultan_connects():
    async def go():
        srv, port = await _serve()
        try:
            r1, w1 = await asyncio.open_connection('127.0.0.1', port)
            b1 = await _welcome(r1)
            await asyncio.sleep(0.1)
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            await asyncio.sleep(0.1)
            # channel 2 waits for a processing slot -> no welcome yet
            blocked = False
            try:
                await asyncio.wait_for(_welcome(r2), timeout=0.5)
            except asyncio.TimeoutError:
                blocked = True
            # free the slot; channel 2 then gets its welcome
            w1.close()
            b2 = await asyncio.wait_for(_welcome(r2), timeout=2)
            w2.close()
            return b1, blocked, b2
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    b1, blocked, b2 = asyncio.run(go())
    assert b1 == MSG_WELCOME
    assert blocked is True
    assert b2 == MSG_WELCOME


def test_030_tcp_3_simultan_connects_and_1_abort():
    async def go():
        srv, port = await _serve()
        try:
            r1, w1 = await asyncio.open_connection('127.0.0.1', port)
            b1 = await _welcome(r1)
            await asyncio.sleep(0.1)
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            await asyncio.sleep(0.1)
            r3, w3 = await asyncio.open_connection('127.0.0.1', port)
            await asyncio.sleep(0.1)
            # channel 3 exceeds max_connections -> abort message + close
            b3 = await asyncio.wait_for(_welcome(r3), timeout=2)
            closed3 = (await r3.read(1)) == b''
            w1.close()
            w2.close()
            w3.close()
            return b1, b3, closed3
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    b1, b3, closed3 = asyncio.run(go())
    assert b1 == MSG_WELCOME
    assert b3 == MSG_ABORT
    assert closed3 is True
