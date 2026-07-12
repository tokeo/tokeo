"""
Ported from the reference suite's test/integration/{self_signed_certificates,
memory_only_certificate,invalid_certificate_name}_test.rb.

Delivery over a TLS_REQUIRED service with: a self-signed cert (simple, with a
chain, and with the key embedded in the chain file), an in-memory generated
cert, and an invalid certificate name (hostname mismatch) that fails.
"""

import os
import ssl
import sys
import shutil
import tempfile
import subprocess

import pytest

from tokeo.core.smtpd.tls import TLS_METHODS_LEGACY, TLS_CIPHERS_LEGACY
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_helpers import (
    read_message, send_mail, run_server_client, insecure_tls_context, verifying_tls_context, make_cert,
)


ENVELOPE_FROM = 'integration@local.local'
ENVELOPE_TO = 'out@local.local'

_CERTS = {}


def _write(path, *pems):
    with open(path, 'wb') as handle:
        for pem in pems:
            handle.write(pem)


def _certs():
    """Generate the test certificates once with cryptography (cached)."""
    if _CERTS:
        return _CERTS
    tmp = tempfile.mkdtemp(prefix='tokeo-smtpd-certtests-')

    def path(name):
        return os.path.join(tmp, name)

    # a self-signed server certificate valid for 127.0.0.1
    _, _, simple_cert, simple_key = make_cert('127.0.0.1', san_ips=['127.0.0.1'])
    _write(path('srv.simple.pem'), simple_cert)
    _write(path('srv.key.pem'), simple_key)
    # a CA and a server certificate signed by it (for the chain cases)
    ca_cert_obj, ca_key_obj, ca_pem, _ = make_cert('Tokeo Test CA', is_ca=True)
    _, _, chain_cert, chain_key = make_cert(
        '127.0.0.1', san_ips=['127.0.0.1'], issuer_cert=ca_cert_obj, issuer_key=ca_key_obj)
    _write(path('ca.pem'), ca_pem)
    _write(path('chain.key.pem'), chain_key)
    # chain file = server cert + CA cert; and a combined chain + key file
    _write(path('chain.pem'), chain_cert, ca_pem)
    _write(path('chain-and-key.pem'), chain_cert, ca_pem, chain_key)
    # an invalid certificate whose name does not match 127.0.0.1
    _, _, invalid_cert, invalid_key = make_cert('invalid.hostname', san_dns=['invalid.hostname'])
    _write(path('invalid.pem'), invalid_cert)
    _write(path('invalid.key.pem'), invalid_key)
    _CERTS.update(
        dir=tmp,
        simple_cert=path('srv.simple.pem'),
        simple_key=path('srv.key.pem'),
        chain_cert=path('chain.pem'),
        chain_key=path('chain.key.pem'),
        chain_and_key=path('chain-and-key.pem'),
        ca=path('ca.pem'),
        invalid_cert=path('invalid.pem'),
        invalid_key=path('invalid.key.pem'),
    )
    return _CERTS


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
    run_server_client(events, settings, lambda port: send_mail(
        port, ENVELOPE_FROM, ENVELOPE_TO, mail,
        authentication_id='administrator', password='password', auth_type='login',
        tls_enabled=True, tls_context=tls_context))
    return mail


def teardown_module(module):
    if _CERTS.get('dir'):
        shutil.rmtree(_CERTS['dir'], ignore_errors=True)


def test_self_signed_simple_certificate():
    certs = _certs()
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['simple_cert'], certs['simple_key'])
    mail = _deliver(ev, settings, verifying_tls_context(certs['simple_cert']))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


@pytest.mark.filterwarnings('ignore::DeprecationWarning')
def test_legacy_tls_preset_still_delivers():
    # the opt-in LEGACY preset lowers the floor to TLS 1.0 and adds SHA1 CBC
    # suites for old clients; a normal (modern) client must still deliver.
    # Python warns that TLS 1.0 is deprecated -- that is the intended signal.
    certs = _certs()
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['simple_cert'], certs['simple_key'])
    settings['tls_methods'] = TLS_METHODS_LEGACY
    settings['tls_ciphers'] = TLS_CIPHERS_LEGACY
    mail = _deliver(ev, settings, verifying_tls_context(certs['simple_cert']))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


def test_self_signed_chain_certificate():
    certs = _certs()
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['chain_cert'], certs['chain_key'])
    mail = _deliver(ev, settings, verifying_tls_context(certs['ca']))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


def test_self_signed_chain_and_key_certificate():
    certs = _certs()
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['chain_and_key'], None)
    mail = _deliver(ev, settings, verifying_tls_context(certs['ca']))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


def test_memory_only_certificate():
    # no cert path -> the server generates a self-signed cert in memory; the
    # client accepts any certificate (VERIFY_NONE)
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


def test_invalid_certificate_name():
    # the certificate name does not match 127.0.0.1 -> the verifying client
    # rejects the handshake with an SSL error
    certs = _certs()
    ev = CaptureSmtpdEvents()
    settings = _tls_settings(certs['invalid_cert'], certs['invalid_key'])
    with pytest.raises(ssl.SSLError):
        _deliver(ev, settings, verifying_tls_context(certs['invalid_cert']))


def test_in_memory_certificate_string():
    # Tokeo-only extension: the certificate and key are provided as PEM
    # strings (tls_cert/tls_key) and loaded in memory (memfd), never via disk
    certs = _certs()
    ev = CaptureSmtpdEvents()
    cert_pem = open(certs['simple_cert']).read()
    key_pem = open(certs['simple_key']).read()
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


# --- self-signed CN/SAN: derivation and configured override ---------------------


def _peer_certificate(settings, hosts=None):
    """Serve with a self-signed cert and return the certificate from the wire."""
    import asyncio
    import smtplib
    from cryptography import x509

    from tokeo.core.smtpd.server import SmtpdServer

    async def go():
        srv = SmtpdServer(CaptureSmtpdEvents(), settings=settings, hosts=hosts)
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
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
    from tokeo.core.smtpd.server import _tls_cert_names

    # a configured cn wins unchanged
    assert _tls_cert_names('mx.example.org', ['alt.example.org'], ['h'], []) == ('mx.example.org', ['alt.example.org'])
    # loopback first -> generic localhost.local (reference behaviour)
    assert _tls_cert_names(None, None, ['127.0.0.1'], [('127.0.0.1', 2525)]) == ('localhost.local', ['127.0.0.1'])
    assert _tls_cert_names(None, None, ['localhost', 'mx.example.org'], [])[0] == 'localhost.local'
    # non-loopback first -> first name becomes the cn; '*' and empties drop
    assert _tls_cert_names(None, None, ['*', 'mx.example.org'], [('10.0.0.5', 25)]) == ('mx.example.org', ['mx.example.org', '10.0.0.5'])
    # nothing usable left -> generic fallback
    assert _tls_cert_names(None, None, ['*'], []) == ('localhost.local', [])


def test_self_signed_derives_names_from_hosts():
    certificate = _peer_certificate({'encrypt_mode': 'TLS_OPTIONAL'}, hosts=['127.0.0.1'])
    cn, dns = _names(certificate)
    assert cn == 'localhost.local'
    assert '127.0.0.1' in dns and 'localhost.local' in dns


def test_self_signed_uses_configured_names():
    certificate = _peer_certificate({
        'encrypt_mode': 'TLS_OPTIONAL',
        'tls_cert_cn': 'mx.tokeo.test',
        'tls_cert_san': 'alt.tokeo.test',
    })
    cn, dns = _names(certificate)
    assert cn == 'mx.tokeo.test'
    assert set(dns) == {'mx.tokeo.test', 'alt.tokeo.test'}


# --- cryptography is lazy: only the self-signed generator needs it --------------


def test_plain_smtpd_runs_without_cryptography():
    # importing and building a server without tls must work when the
    # cryptography package is absent (blocked in a fresh interpreter)
    code = (
        "import sys\n"
        "sys.modules['cryptography'] = None\n"
        "from tokeo.core.smtpd.server import SmtpdServer\n"
        "from tokeo.core.smtpd.events import SmtpdEvents\n"
        "SmtpdServer(SmtpdEvents())\n"
        "print('OK')\n"
    )
    done = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=dict(os.environ))
    assert done.returncode == 0 and done.stdout.strip() == 'OK', done.stderr


def test_self_signed_without_cryptography_raises(monkeypatch):
    from tokeo.core.smtpd.server import SmtpdServer

    monkeypatch.setitem(sys.modules, 'cryptography', None)
    with pytest.raises(ImportError):
        SmtpdServer(CaptureSmtpdEvents(), settings={'encrypt_mode': 'TLS_OPTIONAL'})
