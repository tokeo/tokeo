"""
Tests for a handler without any event method.

The server dispatches only the events a handler has. A bare handler, not
derived from ```SmtpdEvents```, still gets the full dialog: the greeting, a
delivered message, the answers to faults also within DATA, the 421 of a busy
server and the end of a connection by the client, by FIN or by RST.
"""

import asyncio
import socket
import struct

from tests.core.smtpd.lib.smtpd_tests_helper import dialog, read_reply, send_command, start_server, stop_server


class BareEvents:
    """A handler without any event method."""


def test_a_bare_handler_runs_the_full_dialog():
    """Without events a message is delivered, faults are answered by their code."""
    commands = [
        b'EHLO me\r\n',
        b'FOO\r\n',
        b'MAIL FROM:<a@x>\r\n',
        b'RCPT TO:<b@y>\r\n',
        b'DATA\r\n',
        b'Subject: hi\r\n\r\nHello\r\n.\r\n',
        b'MAIL FROM:<a@x>\r\n',
        b'RCPT TO:<b@y>\r\n',
        b'DATA\r\n',
        b'Subject: big\r\n\r\n' + b'x' * 100 + b'\r\n.\r\n',
    ]
    greeting, r = dialog(BareEvents(), {'data_size': 50}, commands)
    assert greeting[0].startswith('220')
    assert r[1][0].startswith('500')
    assert r[5][0].startswith('250')
    assert r[9][0].startswith('552')


def test_a_bare_handler_on_a_busy_server_gets_421():
    """Over max_connections a bare handler answers 421 like any other."""

    async def go():
        server, port, task = await start_server(BareEvents(), {'max_connections': 1, 'max_processings': 1})
        try:
            r1, w1 = await asyncio.open_connection('127.0.0.1', port)
            first = await read_reply(r1)
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            second = await read_reply(r2)
            w1.close()
            w2.close()
            return first, second
        finally:
            await stop_server(server, task)

    first, second = asyncio.run(go())
    assert first[0].startswith('220')
    assert second[0].startswith('421')


def test_a_bare_handler_survives_a_reset_by_the_client():
    """An RST of the client ends the connection, the server serves the next one."""

    async def go():
        server, port, task = await start_server(BareEvents(), {})
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await read_reply(reader)
            await send_command(reader, writer, b'HELO a\r\n')
            # SO_LINGER with 0 seconds closes by RST instead of FIN
            writer.get_extra_info('socket').setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            writer.transport.abort()
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            greeting = await read_reply(reader)
            writer.close()
            return greeting
        finally:
            await stop_server(server, task)

    assert asyncio.run(go())[0].startswith('220')
