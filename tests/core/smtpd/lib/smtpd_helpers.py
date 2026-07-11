"""
Shared test helpers for the smtpd core-lib tests.

Contains the dialog-engine driver (```run_line```, which calls the async
```process_line``` synchronously) and the integration helpers: read an RFC
message from the data folder, generate certificates with ```cryptography```, and
send mail with Python's ```smtplib``` (with optional STARTTLS and AUTH
PLAIN/LOGIN), plus a runner that starts the async server, runs the blocking
client(s) in a thread, and stops the server.
"""

import os
import ssl
import asyncio
import smtplib
import ipaddress
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tokeo.core.smtpd.server import SmtpdServer

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')


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
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
    else:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        # server authentication for the leaf certificate
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    if alt:
        builder = builder.add_extension(x509.SubjectAlternativeName(alt), critical=False)
    # subject key identifier on every cert, and an authority key identifier that
    # points at the issuer's key -- required for strict chain verification
    # (Python 3.13's ssl rejects a chain that is missing these)
    builder = builder.add_extension(
        x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    if issuer_cert is not None:
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_cert.public_key()), critical=False)
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
    with open(os.path.join(DATA_DIR, name), 'r') as handle:
        return handle.read().replace('\r', '').replace('\n', '\r\n')


def insecure_tls_context():
    """A client TLS context that accepts any certificate (VERIFY_NONE)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def verifying_tls_context(ca_file):
    """A client TLS context that verifies the peer and its hostname via ca_file."""
    ctx = ssl.create_default_context(cafile=ca_file)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    # enforce the stricter RFC 5280 checks (default on newer Python/OpenSSL) so
    # the certificate tests behave the same across versions
    ctx.verify_flags |= ssl.VERIFY_X509_STRICT
    return ctx


def send_mail(port, mail_from, rcpt_to, message_data, authentication_id=None, password=None,
              auth_type=None, tls_enabled=False, tls_context=None, ehlo_name='Integration Test client'):
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


async def _run(events, settings, client_fn, listeners, settle):
    srv = SmtpdServer(events, settings=settings)
    await srv.start(listeners or [{'host': '127.0.0.1', 'port': 0}])
    port = srv._servers[0].sockets[0].getsockname()[1]
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
