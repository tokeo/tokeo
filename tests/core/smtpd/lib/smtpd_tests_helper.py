"""
Shared helpers of the tests of tokeo.core.smtpd: the events of the tests
(```RecordingEvents```, ```AuthEvents```), the client dialog against a live
server, the dialog-engine driver (```run_line```, which calls the async
```process_line``` synchronously) and the integration helpers: read an RFC
message from the data folder, generate certificates with ```cryptography```,
and send mail with Python's ```smtplib``` (with optional STARTTLS and AUTH
PLAIN/LOGIN), plus a runner that starts the async server, runs the blocking
client(s) in a thread, and stops the server, and a delivery of raw wire bytes.
"""

import os
import ssl
import base64
import asyncio
import smtplib
import ipaddress
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tokeo.core.smtpd.server import SmtpdServer
from tokeo.core.smtpd.events import SmtpdEvents
from tokeo.core.smtpd.exc import Smtpd550Exception

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')


def run_line(smtpd, session, line, line_break=b'\r\n'):
    """
    Call the async ```process_line``` synchronously (str is encoded to bytes).

    ### Args

    - **smtpd** (SmtpdServer): The server under test
    - **session** (dict): The session hash
    - **line** (str|bytes): The dialog line
    - **line_break** (bytes): The line break passed for DATA mode

    ### Returns

    - **str**: The process_line result

    """
    if isinstance(line, str):
        line = line.encode('utf-8', 'surrogateescape')
    return asyncio.run(smtpd.process_line(session, line, line_break))


# test rules exception: kept in the helper on purpose, more cert tests will use it
def make_cert(common_name, san_ips=(), san_dns=(), issuer_cert=None, issuer_key=None, is_ca=False):
    """
    Generate a certificate and key with ```cryptography``` (no subprocess).

    ### Args

    - **common_name** (str): The subject common name
    - **san_ips** / **san_dns** (iterable): Subject alt name IPs / DNS names
    - **issuer_cert** / **issuer_key**: Sign with this CA (else self-signed)
    - **is_ca** (bool): Mark the certificate as a CA

    ### Returns

    - **tuple**: ```(certificate, key, cert_pem_bytes, key_pem_bytes)```

    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    issuer_name = issuer_cert.subject if issuer_cert is not None else subject
    signing_key = issuer_key if issuer_key is not None else key
    alt = [x509.DNSName(d) for d in san_dns] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in san_ips]
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=90))
    )
    if is_ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        # a CA needs certificate/CRL signing key usage under strict verification
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    else:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        # server authentication for the leaf certificate
        builder = builder.add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    if alt:
        builder = builder.add_extension(x509.SubjectAlternativeName(alt), critical=False)
    # subject key identifier on every cert, and an authority key identifier that
    # points at the issuer's key -- required for strict chain verification
    # (Python 3.13's ssl rejects a chain that is missing these)
    builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    if issuer_cert is not None:
        builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_cert.public_key()), critical=False)
    certificate = builder.sign(signing_key, hashes.SHA256())
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return certificate, key, cert_pem, key_pem


def read_message(name):
    """Read a message file and convert it to RFC CRLF line endings."""
    with open(os.path.join(_DATA_DIR, name), 'r') as handle:
        return handle.read().replace('\r', '').replace('\n', '\r\n')


def insecure_tls_context():
    """A client TLS context that accepts any certificate (VERIFY_NONE)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# test rules exception: kept in the helper on purpose, more cert tests will use it
def verifying_tls_context(ca_file):
    """A client TLS context that verifies the peer and its hostname via ca_file."""
    ctx = ssl.create_default_context(cafile=ca_file)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    # enforce the stricter RFC 5280 checks (default on newer Python/OpenSSL) so
    # the certificate tests behave the same across versions
    ctx.verify_flags |= ssl.VERIFY_X509_STRICT
    return ctx


def send_mail(
    port,
    mail_from,
    rcpt_to,
    message_data,
    authentication_id=None,
    password=None,
    auth_type=None,
    tls_enabled=False,
    tls_context=None,
    ehlo_name='Integration Test client',
):
    """
    Send one mail via smtplib.

    ### Args

    - **port** (int): The server port
    - **mail_from** / **rcpt_to** (str): Envelope addresses
    - **message_data** (str): The message (CRLF); a trailing CRLF is added
    - **authentication_id** / **password** (str, optional): SASL credentials
    - **auth_type** (str, optional): ```'plain'``` or ```'login'```
    - **tls_enabled** (bool): STARTTLS before the dialog
    - **tls_context** (ssl.SSLContext, optional): Client TLS context

    ### Returns

    - **tuple**: The ```(code, response)``` of the final DATA reply

    """
    client = smtplib.SMTP('127.0.0.1', port, timeout=20, local_hostname=ehlo_name)
    try:
        client.ehlo(ehlo_name)
        if tls_enabled:
            client.starttls(context=tls_context or insecure_tls_context())
            client.ehlo(ehlo_name)
        if authentication_id is not None:
            client.user = authentication_id
            client.password = password
            if auth_type == 'login':
                client.auth('LOGIN', client.auth_login)
            else:
                client.auth('PLAIN', client.auth_plain)
        client.mail(mail_from)
        client.rcpt(rcpt_to)
        code, resp = client.data(message_data + '\r\n')
        return code, resp
    finally:
        try:
            client.quit()
        except (smtplib.SMTPException, OSError):
            client.close()


async def start_smtpd(events, listeners=None, **kwargs):
    """Start a server on a free port of 127.0.0.1 by default, return (srv, port)."""
    srv = SmtpdServer(events, **kwargs)
    await srv.start(listeners or [{'host': '127.0.0.1', 'port': 0}])
    return srv, srv._servers[0].sockets[0].getsockname()[1]


async def _run(events, settings, client_fn, listeners, settle):
    srv, port = await start_smtpd(events, listeners, settings=settings)
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, lambda: client_fn(port))
        await asyncio.sleep(settle)
        return result
    finally:
        await srv.stop(wait_seconds_before_close=0.5)


def run_server_client(events, settings, client_fn, listeners=None, settle=0.15):
    """
    Start the server, run a blocking client(port) in a thread, then stop.

    ### Returns

    - **Any**: Whatever ```client_fn(port)``` returned

    """
    return asyncio.run(_run(events, settings, client_fn, listeners, settle))


# --- events and dialog of the tests --------------------------------------------


class RecordingEvents(SmtpdEvents):
    """A handler that records fired events and delivered messages."""

    def __init__(self, options=None):
        super().__init__(options)
        self.fired = []
        self.messages = []
        self.reject_rcpt = set()
        self.rewrite_from = True

    def on_connect_event(self, ctx):
        self.fired.append('connect')

    def on_disconnect_event(self, ctx):
        self.fired.append('disconnect')

    def on_helo_event(self, ctx, helo):
        self.fired.append('helo')

    def on_mail_from_event(self, ctx, mail_from):
        self.fired.append('mail')
        return self.rewrite_from

    def on_rcpt_to_event(self, ctx, rcpt_to):
        self.fired.append('rcpt')
        if any(s in rcpt_to for s in self.reject_rcpt):
            raise Smtpd550Exception
        return True

    def on_message_data_start_event(self, ctx):
        self.fired.append('start')

    def on_message_data_headers_event(self, ctx):
        self.fired.append('headers')

    def on_message_data_receiving_event(self, ctx):
        self.fired.append('recv')

    def on_message_data_event(self, ctx):
        self.messages.append((bytes(ctx.message.data), ctx.envelope.mail_from, list(ctx.envelope.rcpt_tos)))


async def read_reply(reader):
    """Read a full (possibly multi-line) SMTP reply into a list of lines."""
    lines = []
    while True:
        raw = await reader.readuntil(b'\n')
        line = raw.decode('latin-1').rstrip('\r\n')
        lines.append(line)
        if len(line) < 4 or line[3] != '-':
            break
    return lines


async def send_command(reader, writer, line):
    """Send one command line and return the lines of its reply."""
    writer.write(line)
    await writer.drain()
    return await read_reply(reader)


async def start_server(events, settings):
    """Start a server on an ephemeral port; return (server, port, task)."""
    srv = SmtpdServer(events, settings=settings)
    limit = int(settings.get('io_buffer_max_size', 1 << 20))
    server = await asyncio.start_server(srv._on_connection, '127.0.0.1', 0, limit=limit)
    port = server.sockets[0].getsockname()[1]
    task = asyncio.create_task(server.serve_forever())
    return server, port, task


async def stop_server(server, task):
    """Close a server and cancel its serve task."""
    server.close()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _converse(events, settings, commands):
    """Run one client dialog: send each command, read its reply."""
    server, port, task = await start_server(events, settings)
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        greeting = await read_reply(reader)
        replies = []
        for cmd in commands:
            replies.append(await send_command(reader, writer, cmd))
        writer.close()
        await writer.wait_closed()
        return greeting, replies
    finally:
        await stop_server(server, task)


def dialog(events, settings, commands):
    """Synchronous wrapper around a client dialog for pytest."""
    return asyncio.run(_converse(events, settings, commands))


class AuthEvents(RecordingEvents):
    """Accepts one fixed credential pair and returns its authz id."""

    def __init__(self, options=None):
        super().__init__(options)
        self.auth_calls = []
        self.authz_return = 'user'
        self.ctx_auth = None

    def on_auth_event(self, ctx, authorization_id, authentication_id, authentication):
        self.auth_calls.append((authorization_id, authentication_id, authentication))
        if authentication_id == 'user' and authentication == 'secret':
            return self.authz_return
        from tokeo.core.smtpd.exc import Smtpd535Exception

        raise Smtpd535Exception

    def on_mail_from_event(self, ctx, mail_from):
        # capture the auth state as later events see it
        self.ctx_auth = (ctx.server.authenticated, ctx.server.authentication_id, ctx.server.authorization_id)
        return super().on_mail_from_event(ctx, mail_from)


def auth_plain(authzid, authcid, password):
    """The base64 credentials of AUTH PLAIN."""
    return base64.b64encode(f'{authzid}\x00{authcid}\x00{password}'.encode()).decode()


# --- tls client and raw wire ---------------------------------------------------


TLS_SETTINGS = {'encrypt_mode': 'TLS_OPTIONAL', 'tls_cert_cn': 'localhost', 'tls_cert_san': ['127.0.0.1']}


def run_wire(events, settings, wire_body):
    """Deliver one message with raw wire bytes; returns the reply code."""

    async def go():
        srv, port = await start_smtpd(events, settings=settings)
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await reader.readline()
        try:
            for cmd in (b'EHLO x\r\n', b'MAIL FROM:<a@x>\r\n', b'RCPT TO:<b@y>\r\n', b'DATA\r\n'):
                writer.write(cmd)
                await writer.drain()
                line = await reader.readline()
                while line[3:4] == b'-':
                    line = await reader.readline()
            writer.write(wire_body + b'.\r\n')
            await writer.drain()
            reply = await reader.readline()
            writer.write(b'QUIT\r\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return reply[:3]
        finally:
            await srv.stop(wait_seconds_before_close=0.2)

    return asyncio.run(go())
