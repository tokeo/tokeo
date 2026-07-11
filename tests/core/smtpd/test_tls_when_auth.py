"""
Tests for the TLS_WHEN_AUTH encrypt mode (Tokeo-only, beyond the reference).

TLS_WHEN_AUTH behaves like TLS_OPTIONAL for the dialog, but AUTH is only
offered (EHLO) and accepted on an encrypted channel: on plaintext the AUTH
line is hidden and an AUTH command answers 530; after STARTTLS it follows
``auth_mode`` alone. TLS_OPTIONAL itself keeps the reference behaviour
(plaintext AUTH allowed).
"""

import asyncio
import base64

import pytest

from tests.core.smtpd.test_smtpd_server import (
    dialog,
    AuthEvents,
    _serve,
    _reply,
    _stop,
    _tls_client_ctx,
    _TLS_SETTINGS,
)


def _plain(authzid, authcid, password):
    return base64.b64encode(f'{authzid}\x00{authcid}\x00{password}'.encode()).decode()


def _ehlo_auth_and_authcmd(settings):
    banner, r = dialog(AuthEvents(), settings, [
        b'EHLO me\r\n',
        b'AUTH PLAIN ' + _plain('', 'user', 'secret').encode() + b'\r\n',
    ])
    ehlo_lines = r[0]
    advertised = any('AUTH' in line for line in ehlo_lines)
    starttls = any('STARTTLS' in line for line in ehlo_lines)
    return advertised, starttls, r[1][0]


def test_when_auth_on_plain_hides_auth_but_offers_starttls():
    advertised, starttls, reply = _ehlo_auth_and_authcmd({
        'auth_mode': 'AUTH_OPTIONAL',
        'encrypt_mode': 'TLS_WHEN_AUTH',
    })
    assert advertised is False
    assert starttls is True          # the upgrade path is advertised
    assert reply.startswith('530')   # ... and plaintext AUTH is refused


def test_when_auth_after_starttls_offers_and_accepts_auth():
    ev = AuthEvents()
    settings = dict(_TLS_SETTINGS)
    settings.update({'encrypt_mode': 'TLS_WHEN_AUTH', 'auth_mode': 'AUTH_OPTIONAL'})

    async def go():
        server, port, task = await _serve(ev, settings)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            await _reply(reader)
            writer.write(b'EHLO me\r\n')
            await writer.drain()
            ehlo1 = await _reply(reader)
            writer.write(b'STARTTLS\r\n')
            await writer.drain()
            await _reply(reader)
            await writer.start_tls(_tls_client_ctx())
            writer.write(b'EHLO secure\r\n')
            await writer.drain()
            ehlo2 = await _reply(reader)
            writer.write(b'AUTH PLAIN ' + _plain('', 'user', 'secret').encode() + b'\r\n')
            await writer.drain()
            auth_reply = await _reply(reader)
            writer.close()
            await asyncio.sleep(0.05)
            return ehlo1, ehlo2, auth_reply
        finally:
            await _stop(server, task)

    ehlo1, ehlo2, auth_reply = asyncio.run(go())
    assert not any('AUTH' in ln for ln in ehlo1)   # hidden while plaintext
    assert any('AUTH' in ln for ln in ehlo2)       # offered once encrypted
    assert auth_reply[0].startswith('235')         # and accepted


def test_tls_optional_keeps_reference_plaintext_auth():
    # TLS_OPTIONAL stays pure reference behaviour: AUTH advertised and
    # accepted on plaintext
    advertised, _, reply = _ehlo_auth_and_authcmd({
        'auth_mode': 'AUTH_OPTIONAL',
        'encrypt_mode': 'TLS_OPTIONAL',
    })
    assert advertised is True
    assert reply.startswith('235')


def test_tls_required_on_plain_refuses_auth():
    advertised, _, reply = _ehlo_auth_and_authcmd({
        'auth_mode': 'AUTH_OPTIONAL',
        'encrypt_mode': 'TLS_REQUIRED',
    })
    assert advertised is False
    assert reply.startswith('530')


def test_tls_forbidden_offers_plain_auth():
    advertised, starttls, reply = _ehlo_auth_and_authcmd({
        'auth_mode': 'AUTH_OPTIONAL',
        'encrypt_mode': 'TLS_FORBIDDEN',
    })
    assert advertised is True
    assert starttls is False
    assert reply.startswith('235')


def test_parse_error_lists_the_new_mode():
    from tokeo.core.smtpd.server import SmtpdServer
    with pytest.raises(ValueError) as exc:
        SmtpdServer(AuthEvents(), settings={'encrypt_mode': 'TLS_NOPE'})
    assert 'TLS_WHEN_AUTH' in str(exc.value)
