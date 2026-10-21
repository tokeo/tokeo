"""
Tests for tokeo.ext.smtp against the own smtpd as a live server.

The server runs in a background thread with its own event loop, while the
blocking smtplib client of the extension talks to it. Covered are the TLS
connections (STARTTLS and SMTPS) with and without host certificate checks,
the connection end when the dialog fails, and the delivery of a complete
quoted-printable message with attachments, inline image and X-headers.
"""

import os
import ssl
import sys
import time
import email
import asyncio
import smtplib
import platform
import threading
from email.header import decode_header, make_header

import pytest

from tokeo.core.smtpd import SmtpdServer, SmtpdEvents, Smtpd535Exception, Smtpd550Exception
from tests.ext.test_ext_smtp import smtp_app, make_cert, subjMsg, textMsg, htmlImg


_VERSION = sys.version_info[:3]
_SUPPORTED = _VERSION >= (3, 14, 4) or (3, 13, 13) <= _VERSION < (3, 14)

needs_tls_immediate = pytest.mark.skipif(
    not _SUPPORTED,
    reason=f'TLS_IMMEDIATE requires Python 3.13.13+ or 3.14.4+ (gh-142352), running {platform.python_version()}',
)

# the host certificate matches the connected address, the other one does not
HOST_CERT = make_cert('localhost', '127.0.0.1')
OTHER_CERT = make_cert('other.example')


class RecordingEvents(SmtpdEvents):
    """Accepts user/secret, rejects reject@ recipients, records the dialog."""

    def __init__(self, options=None):
        super().__init__(options)
        self.messages = []
        self.disconnected = threading.Event()

    def on_auth_event(self, ctx, authorization_id, authentication_id, authentication):
        if (authentication_id, authentication) != ('user', 'secret'):
            raise Smtpd535Exception

    def on_rcpt_to_event(self, ctx, rcpt_to):
        if 'reject@' in rcpt_to:
            raise Smtpd550Exception

    def on_message_data_event(self, ctx):
        self.messages.append((bytes(ctx.message.data), bool(ctx.server.encrypted)))

    def on_disconnect_event(self, ctx):
        self.disconnected.set()


class BackgroundSmtpd:
    """Run a SmtpdServer in a background thread on an ephemeral port."""

    def __init__(self, settings):
        self.events = RecordingEvents()
        self.server = SmtpdServer(self.events, settings=dict(settings, do_dns_reverse_lookup=False))
        self.loop = asyncio.new_event_loop()
        self.thread = None
        self.port = None

    def __enter__(self):
        started = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.server.start([{'host': '127.0.0.1', 'port': 0}]))
            started.set()
            self.loop.run_forever()
            # let the cancelled serve tasks finish before the loop is closed
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert started.wait(10)
        self.port = self.server._servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *exc):
        asyncio.run_coroutine_threadsafe(self.server.stop(0.5), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
        self.loop.close()


def _tls_settings(mode, cert):
    return {'encrypt_mode': mode, 'tls_cert': cert[0], 'tls_key': cert[1]}


# --- 3. tls end to end -------------------------------------------------------


_MODES = [
    pytest.param('TLS_OPTIONAL', dict(tls=True), id='starttls'),
    pytest.param('TLS_IMMEDIATE', dict(ssl=True), id='ssl', marks=needs_tls_immediate),
]

_CASES = [
    pytest.param(HOST_CERT, dict(host_cert_verify=False), True, id='verify_off'),
    pytest.param(HOST_CERT, dict(host_cert_verify=True), False, id='verify_system_ca'),
    pytest.param(HOST_CERT, dict(host_cert_verify=True, host_cert_ca='file'), True, id='verify_ca_file'),
    pytest.param(HOST_CERT, dict(host_cert_verify=True, host_cert_ca=HOST_CERT[0]), True, id='verify_ca_text'),
    pytest.param(OTHER_CERT, dict(host_cert_verify=True, host_cert_ca=OTHER_CERT[0]), False, id='verify_wrong_host'),
]


@pytest.mark.parametrize('mode, client', _MODES)
@pytest.mark.parametrize('cert, verify, delivered', _CASES)
def test_smtp_tls_to_smtpd(tmp, mode, client, cert, verify, delivered):
    verify = dict(verify)
    if verify.get('host_cert_ca') == 'file':
        verify['host_cert_ca'] = os.path.join(tmp.dir, 'ca.pem')
        with open(verify['host_cert_ca'], 'w') as f:
            f.write(cert[0])

    with BackgroundSmtpd(_tls_settings(mode, cert)) as smtpd:
        with smtp_app(host='127.0.0.1', port=smtpd.port, timeout=5, **client, **verify) as app:
            if delivered:
                assert app.mail.send('body over tls') is True
            else:
                # the self-signed certificate is not trusted by the system, the
                # other one does not match the connected host
                with pytest.raises(ssl.SSLCertVerificationError):
                    app.mail.send('body over tls')

    if delivered:
        assert len(smtpd.events.messages) == 1
        data, encrypted = smtpd.events.messages[0]
        assert email.message_from_bytes(data).get_payload(decode=True) == b'body over tls'
        assert encrypted is True
    else:
        assert smtpd.events.messages == []


# --- 8. connection end on errors ---------------------------------------------


@pytest.mark.parametrize(
    'smtp, error',
    [
        pytest.param(dict(auth=True, auth_identity='user', auth_password='wrong'), smtplib.SMTPAuthenticationError, id='login'),
        pytest.param(dict(to=['reject@example.com']), smtplib.SMTPRecipientsRefused, id='send_message'),
    ],
)
def test_smtp_closes_connection_on_error(smtp, error):
    with BackgroundSmtpd({'auth_mode': 'AUTH_OPTIONAL'}) as smtpd:
        with smtp_app(host='127.0.0.1', port=smtpd.port, timeout=5, **smtp) as app:
            # the original error reaches the caller ...
            with pytest.raises(error):
                app.mail.send('body')
        # ... and the connection was closed anyway
        assert smtpd.events.disconnected.wait(2)
        deadline = time.monotonic() + 2
        while smtpd.server.connections and time.monotonic() < deadline:
            time.sleep(0.01)
        assert smtpd.server.connections == 0

    assert smtpd.events.messages == []


# --- delivery of a complete message ------------------------------------------


def test_smtp_qp_delivered_to_smtpd(tmp):
    pdf = os.path.join(tmp.dir, 'invoice.pdf')
    with open(pdf, 'wb') as f:
        f.write(b'%PDF-1.4 dummy invoice')
    webp = os.path.join(tmp.dir, 'abcd1234.webp')
    with open(webp, 'wb') as f:
        f.write(b'RIFF0000WEBPVP8 dummy')

    with BackgroundSmtpd({}) as smtpd:
        # quoted-printable is set for the config of this app only
        with smtp_app(host='127.0.0.1', port=smtpd.port, timeout=5, body_encoding='qp') as app:
            sent = app.mail.send(
                (textMsg, htmlImg),
                subject=subjMsg,
                files=[
                    ('Demo-Rechnung.pdf', pdf),
                    {'name': 'abcd1234.webp', 'path': webp, 'cid': 'abcd1234.webp'},
                ],
                x_super='Toller Key',
                x_binary=(12, 'hallo', -1),
            )

    assert sent is True
    assert len(smtpd.events.messages) == 1
    data, _ = smtpd.events.messages[0]
    msg = email.message_from_bytes(data)
    parts = {part.get_content_type(): part for part in msg.walk()}

    assert str(make_header(decode_header(msg['Subject']))) == subjMsg
    assert msg['X-super'] == 'Toller Key'
    assert msg['X-binary'] == "(12, 'hallo', -1)"
    # the text arrives quoted-printable with the umlauts encoded as utf-8
    text = parts['text/plain']
    assert text['Content-Transfer-Encoding'] == 'quoted-printable'
    assert '=C3=A4=C3=B6=C3=BC' in text.get_payload()
    assert text.get_payload(decode=True).decode('utf-8') == textMsg
    # the pdf is an attachment, the image is embedded into the related part
    assert parts['application/octet-stream'].get_filename() == 'Demo-Rechnung.pdf'
    image = [p for p in parts['multipart/related'].walk() if p.get_content_type() == 'image/webp'][0]
    assert image['Content-ID'] == '<abcd1234.webp>'
    assert image.get_payload(decode=True) == b'RIFF0000WEBPVP8 dummy'
