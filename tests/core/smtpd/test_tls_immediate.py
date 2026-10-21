"""
Tests for the TLS_IMMEDIATE encrypt mode (SMTPS, Tokeo-only, beyond the reference).

The TLS handshake runs right on connect, or right after the PROXY header with
```proxy_extension```, and the 220 greeting is the first line on the encrypted
channel. The mode needs a runtime whose ```start_tls``` keeps the buffered
ClientHello (CPython gh-142352), so the dialog tests are skipped on older ones.
"""

import sys
import asyncio
import platform

import pytest

from tokeo.core.smtpd.server import SmtpdServer
from tests.core.smtpd.test_smtpd_server import RecordingEvents, _serve, _reply, _stop, _tls_client_ctx


_VERSION = sys.version_info[:3]
_SUPPORTED = _VERSION >= (3, 14, 4) or (3, 13, 13) <= _VERSION < (3, 14)

needs_tls_immediate = pytest.mark.skipif(
    not _SUPPORTED,
    reason=f'TLS_IMMEDIATE requires Python 3.13.13+ or 3.14.4+ (gh-142352), running {platform.python_version()}',
)

_SETTINGS = {'encrypt_mode': 'TLS_IMMEDIATE', 'tls_cert_cn': 'localhost', 'tls_cert_san': ['127.0.0.1']}


class ImmediateEvents(RecordingEvents):
    """Records the encryption and proxy state as seen at MAIL time."""

    def __init__(self, options=None):
        super().__init__(options)
        self.encrypted_at_mail = None
        self.proxy_at_mail = None

    async def on_connect_event(self, ctx):
        # give the client time to put its ClientHello into the read buffer
        await asyncio.sleep(0.2)

    def on_mail_from_event(self, ctx, mail_from):
        self.encrypted_at_mail = ctx.server.encrypted
        self.proxy_at_mail = ctx.server.proxy
        return super().on_mail_from_event(ctx, mail_from)


async def _deliver(reader, writer):
    """Run EHLO, STARTTLS and one transaction; return the replies."""
    replies = []
    for cmd in (b'EHLO me\r\n', b'STARTTLS\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n', b'body\r\n.\r\n'):
        writer.write(cmd)
        await writer.drain()
        replies.append(await _reply(reader))
    return replies


@needs_tls_immediate
def test_tls_immediate_delivers_over_tls():
    ev = ImmediateEvents()

    async def go():
        server, port, task = await _serve(ev, _SETTINGS)
        try:
            # the client speaks TLS from the first byte on
            reader, writer = await asyncio.open_connection('127.0.0.1', port, ssl=_tls_client_ctx())
            greeting = await _reply(reader)
            replies = await _deliver(reader, writer)
            writer.close()
            await asyncio.sleep(0.05)
            return greeting, replies
        finally:
            await _stop(server, task)

    greeting, (ehlo, starttls, *_, final) = asyncio.run(go())
    assert greeting[0].startswith('220')
    # already encrypted -> STARTTLS is neither offered nor accepted
    assert not any('STARTTLS' in line for line in ehlo)
    assert starttls[0].startswith('503')
    assert final[0].startswith('250')
    assert ev.messages and ev.messages[0][0] == b'body'
    assert ev.encrypted_at_mail


@needs_tls_immediate
def test_tls_immediate_behind_proxy():
    ev = ImmediateEvents()

    async def go():
        server, port, task = await _serve(ev, dict(_SETTINGS, proxy_extension=True))
        try:
            # PROXY header and ClientHello are buffered together by the server,
            # the handshake must still see the whole ClientHello
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(b'PROXY TCP4 9.8.7.6 1.2.3.4 4711 465\r\n')
            await writer.drain()
            await writer.start_tls(_tls_client_ctx())
            greeting = await _reply(reader)
            replies = await _deliver(reader, writer)
            writer.close()
            await asyncio.sleep(0.05)
            return greeting, replies
        finally:
            await _stop(server, task)

    greeting, replies = asyncio.run(go())
    assert greeting[0].startswith('220')
    assert replies[-1][0].startswith('250')
    assert ev.encrypted_at_mail
    assert ev.proxy_at_mail['source_ip'] == '9.8.7.6'


@pytest.mark.parametrize(
    'version, supported',
    [
        ((3, 13, 12), False),
        ((3, 14, 3), False),
        ((3, 13, 13), True),
        ((3, 15, 0), True),
    ],
    ids=lambda value: '.'.join(map(str, value)) if isinstance(value, tuple) else str(value),
)
def test_tls_immediate_python_version(monkeypatch, version, supported):
    monkeypatch.setattr('tokeo.core.smtpd.server.sys.version_info', version + ('final', 0))
    if supported:
        assert SmtpdServer(RecordingEvents(), settings=_SETTINGS).tls is not None
    else:
        with pytest.raises(ValueError, match='requires Python 3.13.13\\+ or 3.14.4\\+'):
            SmtpdServer(RecordingEvents(), settings=_SETTINGS)
