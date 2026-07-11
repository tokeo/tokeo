"""
Ported from midi-smtp-server test/integration/{self_signed_certificates,
memory_only_certificate,invalid_certificate_name}_test.rb.

Delivery over a TLS_REQUIRED service with: a self-signed cert (simple, with a
chain, and with the key embedded in the chain file), an in-memory generated
cert, and an invalid certificate name (hostname mismatch) that fails.
"""

import os
import ssl
import shutil
import tempfile

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
    # Tokeo extension beyond midi: the certificate and key are provided as PEM
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
