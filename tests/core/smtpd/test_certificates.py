"""
Ported from the reference suite's test/integration/{self_signed_certificates,
memory_only_certificate,invalid_certificate_name}_test.rb.

Delivery over a TLS_REQUIRED service with: a self-signed cert (simple, with a
chain, and with the key embedded in the chain file), an in-memory generated
cert, and an invalid certificate name (hostname mismatch) that fails. The
generated test certificate carries an EC P-256 key.
"""

import os
import ssl
import sys
import subprocess

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from tokeo.core.smtpd.tls import TlsTransport, TLS_METHODS_LEGACY, TLS_CIPHERS_LEGACY
from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_tests_helper import (
    read_message,
    send_mail,
    run_server_client,
    insecure_tls_context,
    verifying_tls_context,
    make_cert,
    start_smtpd,
)

ENVELOPE_FROM = 'integration@local.local'
ENVELOPE_TO = 'out@local.local'


def _write(path, *pems):
    with open(path, 'wb') as handle:
        for pem in pems:
            handle.write(pem)


@pytest.fixture(scope='module')
def certs(tmp_path_factory):
    """Generate the test certificates once for the module with cryptography."""
    tmp = tmp_path_factory.mktemp('certs')

    def path(name):
        return os.path.join(tmp, name)

    # a self-signed server certificate valid for 127.0.0.1
    _, _, simple_cert, simple_key = make_cert('127.0.0.1', san_ips=['127.0.0.1'])
    _write(path('srv.simple.pem'), simple_cert)
    _write(path('srv.key.pem'), simple_key)
    # a CA and a server certificate signed by it (for the chain cases)
    ca_cert_obj, ca_key_obj, ca_pem, _ = make_cert('Tokeo Test CA', is_ca=True)
    _, _, chain_cert, chain_key = make_cert('127.0.0.1', san_ips=['127.0.0.1'], issuer_cert=ca_cert_obj, issuer_key=ca_key_obj)
    _write(path('ca.pem'), ca_pem)
    _write(path('chain.key.pem'), chain_key)
    # chain file = server cert + CA cert; and a combined chain + key file
    _write(path('chain.pem'), chain_cert, ca_pem)
    _write(path('chain-and-key.pem'), chain_cert, ca_pem, chain_key)
    # an invalid certificate whose name does not match 127.0.0.1
    _, _, invalid_cert, invalid_key = make_cert('invalid.hostname', san_dns=['invalid.hostname'])
    _write(path('invalid.pem'), invalid_cert)
    _write(path('invalid.key.pem'), invalid_key)
    return dict(
        simple_cert=path('srv.simple.pem'),
        simple_key=path('srv.key.pem'),
        chain_cert=path('chain.pem'),
        chain_key=path('chain.key.pem'),
        chain_and_key=path('chain-and-key.pem'),
        ca=path('ca.pem'),
        invalid_cert=path('invalid.pem'),
        invalid_key=path('invalid.key.pem'),
    )


def _tls_settings(cert_path, key_path):
    return {
        'max_processings': 1,
        'do_dns_reverse_lookup': False,
        'auth_mode': 'AUTH_OPTIONAL',
        'encrypt_mode': 'TLS_REQUIRED',
        'tls_cert_path': cert_path,
        'tls_key_path': key_path,
        'pipelining_extension': False,
        'internationalization_extensions': True,
    }


def _deliver(events, settings, tls_context):
    mail = read_message('simple_mail.msg')
    run_server_client(
        events,
        settings,
        lambda port: send_mail(
            port,
            ENVELOPE_FROM,
            ENVELOPE_TO,
            mail,
            authentication_id='administrator',
            password='password',
            auth_type='login',
            tls_enabled=True,
            tls_context=tls_context,
        ),
    )
    return mail


def test_self_signed_simple_certificate(certs):
    """A self-signed certificate from files delivers to a verifying client."""
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['simple_cert'], certs['simple_key'])
    mail = _deliver(ev, settings, verifying_tls_context(certs['simple_cert']))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


@pytest.mark.filterwarnings('ignore::DeprecationWarning')
def test_legacy_tls_preset_still_delivers(certs):
    """The LEGACY preset, down to TLS 1.0, still delivers to a modern client."""
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['simple_cert'], certs['simple_key'])
    settings['tls_methods'] = TLS_METHODS_LEGACY
    settings['tls_ciphers'] = TLS_CIPHERS_LEGACY
    mail = _deliver(ev, settings, verifying_tls_context(certs['simple_cert']))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


def test_self_signed_chain_certificate(certs):
    """A certificate with its chain delivers to a client trusting the CA."""
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['chain_cert'], certs['chain_key'])
    mail = _deliver(ev, settings, verifying_tls_context(certs['ca']))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


def test_self_signed_chain_and_key_certificate(certs):
    """A chain file with the key in it delivers without a key file."""
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['chain_and_key'], None)
    mail = _deliver(ev, settings, verifying_tls_context(certs['ca']))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


def test_memory_only_certificate():
    """Without a certificate the server makes one in memory and delivers."""
    ev = CaptureSmtpdEvents()
    settings = {
        'max_processings': 1,
        'do_dns_reverse_lookup': False,
        'auth_mode': 'AUTH_OPTIONAL',
        'encrypt_mode': 'TLS_REQUIRED',
        'tls_cert_cn': '127.0.0.1',
        'tls_cert_san': ['127.0.0.1'],
        'pipelining_extension': False,
        'internationalization_extensions': True,
    }
    mail = _deliver(ev, settings, insecure_tls_context())
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


def test_invalid_certificate_name(certs):
    """A certificate not naming 127.0.0.1 fails the handshake by SSLError."""
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['invalid_cert'], certs['invalid_key'])
    with pytest.raises(ssl.SSLError):
        _deliver(ev, settings, verifying_tls_context(certs['invalid_cert']))


def test_in_memory_certificate_string(certs):
    """Certificate and key given as PEM strings deliver, read from memory."""
    ev = CaptureSmtpdEvents()
    with open(certs['simple_cert']) as handle:
        cert_pem = handle.read()
    with open(certs['simple_key']) as handle:
        key_pem = handle.read()
    settings = {
        'max_processings': 1,
        'do_dns_reverse_lookup': False,
        'auth_mode': 'AUTH_OPTIONAL',
        'encrypt_mode': 'TLS_REQUIRED',
        'tls_cert': cert_pem,
        'tls_key': key_pem,
        'pipelining_extension': False,
        'internationalization_extensions': True,
    }
    mail = _deliver(ev, settings, verifying_tls_context(certs['simple_cert']))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


# ---- self-signed CN/SAN: derivation and configured override --------------------


def _peer_certificate(settings, hosts=None):
    """Serve with a self-signed cert and return the certificate from the wire."""
    import asyncio
    import smtplib
    from cryptography import x509

    async def go():
        srv, port = await start_smtpd(CaptureSmtpdEvents(), settings=settings, hosts=hosts)
        loop = asyncio.get_event_loop()

        def client():
            client = smtplib.SMTP('127.0.0.1', port, timeout=10)
            client.starttls(context=insecure_tls_context())
            der = client.sock.getpeercert(binary_form=True)
            client.close()
            return der

        try:
            return await loop.run_in_executor(None, client)
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    return x509.load_der_x509_certificate(asyncio.run(go()))


def _names(certificate):
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    cn = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return cn, sans.get_values_for_type(x509.DNSName)


def test_tls_cert_names_derivation_rules():
    """CN and SAN come from the config, else from the first usable host."""
    from tokeo.core.smtpd.server import _tls_cert_names

    # a configured cn wins unchanged
    assert _tls_cert_names('mx.example.org', ['alt.example.org'], ['h'], []) == ('mx.example.org', ['alt.example.org'])
    # loopback first -> generic localhost.local (reference behaviour)
    assert _tls_cert_names(None, None, ['127.0.0.1'], [('127.0.0.1', 2525)]) == ('localhost.local', ['127.0.0.1'])
    assert _tls_cert_names(None, None, ['localhost', 'mx.example.org'], [])[0] == 'localhost.local'
    # non-loopback first -> first name becomes the cn; '*' and empties drop,
    # the names of the local host follow for the wildcard
    cn, sans = _tls_cert_names(None, None, ['*', 'mx.example.org'], [('10.0.0.5', 25)])
    assert cn == 'mx.example.org'
    assert sans[:2] == ['mx.example.org', '10.0.0.5'] and 'localhost' in sans
    # nothing usable left -> generic fallback
    assert _tls_cert_names(None, None, [], []) == ('localhost.local', [])


def test_self_signed_derives_names_from_hosts():
    """Bound to 127.0.0.1 the certificate is localhost.local with the ip."""
    certificate = _peer_certificate({'encrypt_mode': 'TLS_OPTIONAL'}, hosts=['127.0.0.1'])
    cn, dns = _names(certificate)
    assert cn == 'localhost.local'
    assert '127.0.0.1' in dns and 'localhost.local' in dns


def test_self_signed_uses_configured_names():
    """tls_cert_cn and tls_cert_san become the CN and the SAN."""
    certificate = _peer_certificate(
        {
            'encrypt_mode': 'TLS_OPTIONAL',
            'tls_cert_cn': 'mx.tokeo.test',
            'tls_cert_san': 'alt.tokeo.test',
        }
    )
    cn, dns = _names(certificate)
    assert cn == 'mx.tokeo.test'
    assert set(dns) == {'mx.tokeo.test', 'alt.tokeo.test'}


# ---- cryptography is lazy: only the self-signed generator needs it -------------


def test_plain_smtpd_runs_without_cryptography():
    """Without cryptography a server without TLS still builds."""
    code = (
        'import sys\n'
        "sys.modules['cryptography'] = None\n"
        'from tokeo.core.smtpd.server import SmtpdServer\n'
        'from tokeo.core.smtpd.events import SmtpdEvents\n'
        'SmtpdServer(SmtpdEvents())\n'
        "print('OK')\n"
    )
    done = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=dict(os.environ))
    assert done.returncode == 0 and done.stdout.strip() == 'OK', done.stderr


def test_self_signed_without_cryptography_raises(monkeypatch):
    """Without cryptography a server needing a certificate raises."""
    from tokeo.core.smtpd.server import SmtpdServer

    monkeypatch.setitem(sys.modules, 'cryptography', None)
    with pytest.raises(ImportError):
        SmtpdServer(CaptureSmtpdEvents(), settings={'encrypt_mode': 'TLS_OPTIONAL'})


def test_generated_test_certificate_has_an_ec_p256_key():
    """The generated self-signed test certificate carries an EC P-256 key."""
    lines = []
    TlsTransport(logger=lambda severity, msg: lines.append(msg))
    pem = next(msg for msg in lines if msg.startswith('SSL: generated test certificate')).split('\n', 1)[1]
    key = x509.load_pem_x509_certificate(pem.encode()).public_key()
    assert isinstance(key, ec.EllipticCurvePublicKey) and key.curve.name == 'secp256r1'


def test_missing_certificate_or_key_file_stops_the_setup(certs, tmp_path):
    """A certificate or key path without a file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match='certificate'):
        TlsTransport(cert_path=str(tmp_path / 'missing.pem'))
    with pytest.raises(FileNotFoundError, match='private key'):
        TlsTransport(cert_path=certs['simple_cert'], key_path=str(tmp_path / 'missing.key'))


def test_a_failing_logger_does_not_break_the_setup():
    """A logger that raises is ignored, the self-signed certificate still loads."""

    def broken(severity, msg):
        raise RuntimeError('logger down')

    tls = TlsTransport(cert_cn='localhost', logger=broken)
    assert tls.context is not None


def test_a_transport_without_a_logger_loads_its_certificate():
    """Without a logger the self-signed certificate loads as well."""
    tls = TlsTransport(cert_cn='localhost')
    assert tls.context is not None
