"""
Ported from midi-smtp-server test/integration/send_mails_test.rb.

Real-life delivery over TCP with smtplib: plain send, ten sends, AUTH PLAIN and
AUTH LOGIN with delivery, an AUTH failure, and a STARTTLS + AUTH + delivery.
"""

import smtplib

import pytest

from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_helpers import read_message, send_mail, run_server_client, insecure_tls_context


ENVELOPE_FROM = 'integration@local.local'
ENVELOPE_TO = 'out@local.local'

SETTINGS = {
    'max_processings': 1,
    'do_dns_reverse_lookup': False,
    'auth_mode': 'AUTH_OPTIONAL',
    'encrypt_mode': 'TLS_OPTIONAL',
    'tls_cert_cn': '127.0.0.1',
    'tls_cert_san': ['127.0.0.1'],
    'pipelining_extension': False,
    'internationalization_extensions': True,
}


def test_net_smtp_simple_send_1_mail():
    ev = CaptureSmtpdEvents()
    mail = read_message('simple_mail.msg')
    run_server_client(ev, SETTINGS, lambda port: send_mail(port, ENVELOPE_FROM, ENVELOPE_TO, mail))
    assert ev.ev_message_data == mail.encode()


def test_net_smtp_simple_send_10_mails():
    ev = CaptureSmtpdEvents()
    mail = read_message('simple_mail.msg')

    def client(port):
        for _ in range(10):
            send_mail(port, ENVELOPE_FROM, ENVELOPE_TO, mail)

    run_server_client(ev, SETTINGS, client)
    assert ev.ev_message_data == mail.encode()


def test_net_smtp_auth_plain_and_simple_send_1_mail():
    ev = CaptureSmtpdEvents()
    mail = read_message('simple_mail.msg')
    run_server_client(ev, SETTINGS, lambda port: send_mail(
        port, ENVELOPE_FROM, ENVELOPE_TO, mail,
        authentication_id='administrator', password='password', auth_type='plain'))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


def test_net_smtp_auth_login_and_simple_send_1_mail():
    ev = CaptureSmtpdEvents()
    mail = read_message('simple_mail.msg')
    run_server_client(ev, SETTINGS, lambda port: send_mail(
        port, ENVELOPE_FROM, ENVELOPE_TO, mail,
        authentication_id='administrator', password='password', auth_type='login'))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'


def test_net_smtp_auth_plain_fail():
    ev = CaptureSmtpdEvents()
    mail = read_message('simple_mail.msg')
    with pytest.raises(smtplib.SMTPAuthenticationError):
        run_server_client(ev, SETTINGS, lambda port: send_mail(
            port, ENVELOPE_FROM, ENVELOPE_TO, mail,
            authentication_id='administrator', password='error_password', auth_type='plain'))


def test_mikel_mail_simple_send_1_mail():
    # midi drives this via the Mail gem; Python has one SMTP client, so this
    # mirrors the plain send as a second, independent delivery
    ev = CaptureSmtpdEvents()
    mail = read_message('simple_mail.msg')
    run_server_client(ev, SETTINGS, lambda port: send_mail(port, ENVELOPE_FROM, ENVELOPE_TO, mail))
    assert ev.ev_message_data == mail.encode()


def test_mikel_mail_simple_send_1_mail_starttls():
    ev = CaptureSmtpdEvents()
    mail = read_message('simple_mail.msg')
    run_server_client(ev, SETTINGS, lambda port: send_mail(
        port, ENVELOPE_FROM, ENVELOPE_TO, mail,
        authentication_id='administrator', password='password', auth_type='login',
        tls_enabled=True, tls_context=insecure_tls_context()))
    assert ev.ev_message_data == mail.encode()
    assert ev.ev_auth_authorization_id == 'supervisor'
