"""
Tests for tokeo.ext.smtp without a server: the built messages (encodings,
headers, attachments, inline images, mime structure) and the smtplib calls.
"""

import os
import ssl
import base64
from unittest import mock

import pytest
from tests.ext.smtp.smtp_tests_helper import SmtpTest, make_cert, subjMsg, textMsg, htmlImg


def test_qp_message(tmp):
    """In qp the umlauts of the body arrive as =C3=A4=C3=B6=C3=BC."""
    pdf = os.path.join(tmp.dir, 'invoice.pdf')
    with open(pdf, 'wb') as f:
        f.write(b'%PDF-1.4 dummy invoice')
    webp = os.path.join(tmp.dir, 'abcd1234.webp')
    with open(webp, 'wb') as f:
        f.write(b'RIFF0000WEBPVP8 dummy')

    with SmtpTest(body_encoding='qp', from_addr='Tester <tester@example.com>', to='dev@example.com') as app:
        mail = app.mail
        body = (textMsg, htmlImg)
        get_params = mail._get_params(
            to=mail._config('to'),
            subject=subjMsg,
            files=[
                ('Demo-Rechnung.pdf', pdf),
                {'name': 'abcd1234.webp', 'path': webp, 'cid': 'abcd1234.webp'},
            ],
        )
        msg = mail._make_message(body, **get_params).as_string()
        assert 'Content-Transfer-Encoding: quoted-printable' in msg
        # the umlauts of the plain body must arrive utf-8 quoted-printable
        assert '=C3=A4=C3=B6=C3=BC' in msg
        assert 'Demo-Rechnung.pdf' in msg and 'abcd1234.webp' in msg


# --- helpers for the tests below ---------------------------------------------


# a valid 1x1 png, MIMEImage detects it as image/png
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def _parts(msg):
    """All leaf parts of a message as list of (content type, part)."""
    return [(part.get_content_type(), part) for part in msg.walk() if not part.is_multipart()]


# --- connection parameters ---------------------------------------------------


@pytest.mark.parametrize('use_ssl', [False, True], ids=['smtp', 'smtp_ssl'])
def test_connection_params(use_ssl):
    """
    local_hostname and source_address come from the config only, an empty
    value becomes None and the source port is left to the system.
    """
    with mock.patch('smtplib.SMTP') as smtp, mock.patch('smtplib.SMTP_SSL') as smtp_ssl:
        with SmtpTest(ssl=use_ssl, timeout=7, local_hostname='mx.local', source_address='127.0.0.1') as app:
            app.mail.send('body', local_hostname='other.local', source_address='10.0.0.1')
        with SmtpTest(ssl=use_ssl, local_hostname='', source_address='') as app:
            app.mail.send('body')

    used, unused = (smtp_ssl, smtp) if use_ssl else (smtp, smtp_ssl)
    assert unused.call_count == 0
    expected = dict(local_hostname='mx.local', timeout=7, source_address=('127.0.0.1', 0))
    empty = dict(local_hostname=None, timeout=30, source_address=None)
    if use_ssl:
        # without host_cert_verify the smtplib default context is kept
        expected['context'] = empty['context'] = None
    assert used.call_args_list[0] == mock.call('localhost', '25', **expected)
    assert used.call_args_list[1] == mock.call('localhost', '25', **empty)


def test_starttls_and_login():
    """tls with host_cert_verify uses a verifying context, then logs in."""
    with mock.patch('smtplib.SMTP') as smtp:
        with SmtpTest(tls=True, auth=True, auth_identity='user', auth_password='secret', host_cert_verify=True) as app:
            app.mail.send('body')

    server = smtp.return_value
    context = server.starttls.call_args.kwargs['context']
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
    server.login.assert_called_once_with('user', 'secret')
    server.send_message.assert_called_once()


# --- host certificate context ------------------------------------------------


def test_host_cert_context(tmp):
    """host_cert_ca takes PEM text or a file path, used strictly as given."""
    cert_pem, _ = make_cert('localhost')
    ca_file = os.path.join(tmp.dir, 'ca.pem')
    with open(ca_file, 'w') as f:
        f.write(cert_pem)

    with SmtpTest() as app:
        context = app.mail._host_cert_context
        # verify off keeps the smtplib default
        assert context(host_cert_verify=False, host_cert_ca=cert_pem) is None
        # verify on without CA trusts the system CAs
        system = context(host_cert_verify=True, host_cert_ca=None)
        assert system.verify_mode == ssl.CERT_REQUIRED and system.check_hostname is True
        # multi line is PEM text, single line is a file path
        for ca in (cert_pem, ca_file):
            trusted = context(host_cert_verify=True, host_cert_ca=ca)
            assert [c['subject'] for c in trusted.get_ca_certs()] == [((('commonName', 'localhost'),),)]
        # the value is used strictly as given
        with pytest.raises(ssl.SSLError):
            context(host_cert_verify=True, host_cert_ca=ca_file + '\n')
        with pytest.raises(FileNotFoundError):
            context(host_cert_verify=True, host_cert_ca=os.path.join(tmp.dir, 'missing.pem'))


# --- x-headers ---------------------------------------------------------------


@pytest.mark.parametrize(
    'header_encoding, encoded',
    [(None, '1'), ('base64', '=?utf-8?b?MQ==?='), ('qp', '=?utf-8?q?1?=')],
)
def test_x_headers(header_encoding, encoded):
    """x_ arguments become X- headers, encoded like the subject."""
    with SmtpTest(header_encoding=header_encoding) as app:
        params = app.mail._get_params(X_API_Key='1', x_my_header='2')
        msg = app.mail._make_message('body', **params)

    # the name is kept as given, only the prefix separator becomes a dash;
    # with header_encoding the value is encoded like the subject
    assert 'X-my-header' in msg.keys()
    assert f'X-API-Key: {encoded}\n' in msg.as_string()


# --- attachments -------------------------------------------------------------


def test_attachment_names(tmp):
    """An attachment keeps a given name, else the name of its file."""
    pdf = os.path.join(tmp.dir, 'invoice.pdf')
    with open(pdf, 'wb') as f:
        f.write(b'%PDF-1.4 dummy')

    with SmtpTest() as app:
        files = [('docs/2026/q3.pdf', pdf), (None, pdf), {'path': pdf}]
        msg = app.mail._make_message('body', **app.mail._get_params(files=files))

    names = [part.get_filename() for _, part in _parts(msg) if part.get_filename()]
    # an alternative name is used as given, without one the file name applies
    assert names == ['docs/2026/q3.pdf', 'invoice.pdf', 'invoice.pdf']


def test_inline_image(tmp):
    """With html a cid file is an inline image, without html an attachment."""
    png = os.path.join(tmp.dir, 'logo.png')
    with open(png, 'wb') as f:
        f.write(PNG)
    files = [{'path': png, 'cid': 'logo'}]

    with SmtpTest() as app:
        html = app.mail._make_message(('text', '<img src="cid:logo">'), **app.mail._get_params(files=files))
        text = app.mail._make_message('text', **app.mail._get_params(files=files))

    # with html the image is embedded into the related part
    related = [p for p in html.walk() if p.get_content_type() == 'multipart/related'][0]
    image = [p for p in related.walk() if p.get_content_type() == 'image/png'][0]
    assert image['Content-ID'] == '<logo>'
    assert image['Content-Disposition'] == 'inline; filename=logo.png'
    # the image part is encoded once, with exactly one transfer encoding header
    assert image.get_all('Content-Transfer-Encoding') == ['base64']
    assert image.get_payload(decode=True) == PNG
    # without html the file is a plain attachment
    attachment = [p for _, p in _parts(text) if p.get_filename()][0]
    assert attachment.get_content_type() == 'application/octet-stream'
    assert attachment['Content-Disposition'] == 'attachment; filename=logo.png'
    assert attachment['Content-ID'] is None


def test_inline_image_unsupported(tmp):
    """An svg as inline image raises TypeError."""
    svg = os.path.join(tmp.dir, 'logo.svg')
    with open(svg, 'w') as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg"/>')

    with SmtpTest() as app:
        params = app.mail._get_params(files=[{'path': svg, 'cid': 'logo'}])
        with pytest.raises(TypeError, match='Not a supported image: .*logo.svg at inline attachment'):
            app.mail._make_message(('text', '<img src="cid:logo">'), **params)


# --- mime structure ----------------------------------------------------------


@pytest.mark.parametrize(
    'body, with_file, content_type',
    [
        ('text', False, 'text/plain'),
        ({'html': '<p>html</p>'}, False, 'text/html'),
        (('text', '<p>html</p>'), False, 'multipart/alternative'),
        ('text', True, 'multipart/mixed'),
        (('text', '<p>html</p>'), True, 'multipart/mixed'),
    ],
    ids=['text', 'html', 'alternative', 'mixed', 'mixed_related'],
)
def test_mime_structure(tmp, body, with_file, content_type):
    """The body and files decide text, html, alternative or mixed."""
    pdf = os.path.join(tmp.dir, 'invoice.pdf')
    with open(pdf, 'wb') as f:
        f.write(b'%PDF-1.4 dummy')

    with SmtpTest() as app:
        msg = app.mail._make_message(body, **app.mail._get_params(files=[pdf] if with_file else None))

    assert msg.get_content_type() == content_type
    # html with files is wrapped into a related part for inline images
    related = [p for p in msg.walk() if p.get_content_type() == 'multipart/related']
    assert len(related) == (1 if with_file and isinstance(body, tuple) else 0)


# --- invalid body ------------------------------------------------------------


@pytest.mark.parametrize('body', [5, {'text': None}, (5, '')], ids=['int', 'dict_none', 'tuple_int'])
def test_invalid_body(body):
    """An int or a None part as body raises TypeError."""
    with SmtpTest() as app:
        with pytest.raises(TypeError):
            app.mail._make_message(body, **app.mail._get_params())


# --- date and message-id -----------------------------------------------------


def test_date_and_message_id():
    """Date and Message-Id are set, a given value wins, both can be left out."""
    with SmtpTest(msgid_domain='example.com') as app:
        enforced = app.mail._make_message('body', **app.mail._get_params())
        given = app.mail._make_message(
            'body', **app.mail._get_params(date='Mon, 01 Jan 2024 00:00:00 +0000', message_id='<given@example.com>')
        )
        absent = app.mail._make_message('body', **app.mail._get_params(date_enforce=False, msgid_enforce=False))

    assert enforced['Date'] and enforced['Message-Id'].endswith('@example.com>')
    # a given value wins over the generated one
    assert given['Date'] == 'Mon, 01 Jan 2024 00:00:00 +0000'
    assert given['Message-Id'] == '<given@example.com>'
    assert absent['Date'] is None and absent['Message-Id'] is None
