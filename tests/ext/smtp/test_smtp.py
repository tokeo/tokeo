"""
Tests for tokeo.ext.smtp without a server: the built messages (encodings,
headers, attachments, inline images, mime structure) and the smtplib calls.
"""

import os
import ssl
import base64
import email
import email.policy
from unittest import mock

import pytest
import tokeo.main
from tokeo.ext.smtp import TokeoSmtpError
from tests.ext.smtp.smtp_tests_helper import SmtpTest, SmtpTemplateTest, make_cert, SUBJECT, TEXT, HTML_WITH_IMAGE


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
        body = (TEXT, HTML_WITH_IMAGE)
        get_params = mail._get_params(
            to=mail._config('to'),
            subject=SUBJECT,
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


# ---- helpers for the tests below --------------------------------------------


# a valid 1x1 png, MIMEImage detects it as image/png
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def _get_parts(msg):
    """All leaf parts of a message as list of (content type, part)."""
    return [(part.get_content_type(), part) for part in msg.walk() if not part.is_multipart()]


# ---- connection parameters --------------------------------------------------


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

    used_mock, unused_mock = (smtp_ssl, smtp) if use_ssl else (smtp, smtp_ssl)
    assert unused_mock.call_count == 0
    expected = dict(local_hostname='mx.local', timeout=7, source_address=('127.0.0.1', 0))
    empty = dict(local_hostname=None, timeout=30, source_address=None)
    if use_ssl:
        # without tls_verify_cert the smtplib default context is kept
        expected['context'] = empty['context'] = None
    assert used_mock.call_args_list[0] == mock.call('localhost', 25, **expected)
    assert used_mock.call_args_list[1] == mock.call('localhost', 25, **empty)


def test_starttls_and_login():
    """tls with tls_verify_cert uses a verifying context, then logs in."""
    with mock.patch('smtplib.SMTP') as smtp:
        with SmtpTest(tls=True, auth=True, auth_identity='user', auth_password='secret', tls_verify_cert=True) as app:
            app.mail.send('body')

    server = smtp.return_value
    context = server.starttls.call_args.kwargs['context']
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
    server.login.assert_called_once_with('user', 'secret')
    server.send_message.assert_called_once()


@pytest.mark.parametrize('auth', [True, False], ids=['auth', 'no_auth'])
def test_debug_prints_the_dialog_and_warns_with_auth(auth):
    """Debug prints the whole dialog, with auth a warning names the credentials."""
    warnings = []
    with mock.patch('smtplib.SMTP') as smtp:
        with SmtpTest(auth=auth, auth_identity='user', auth_password='secret') as app:
            app._meta.debug = True
            with mock.patch.object(app.log, 'warning', side_effect=warnings.append):
                app.mail.send('body')
    smtp.return_value.set_debuglevel.assert_called_once_with(9)
    assert any('AUTH credentials included' in msg for msg in warnings) is auth


# ---- tls context ------------------------------------------------------------


def _create_tls_context(app, tls_verify_cert=True, tls_verify_hostname=True, tls_ca_path=None, tls_ca=None):
    # the tls context of the handler for the given options
    return app.mail._create_tls_context(
        tls_verify_cert=tls_verify_cert, tls_verify_hostname=tls_verify_hostname, tls_ca_path=tls_ca_path, tls_ca=tls_ca
    )


def test_create_tls_context(tmp):
    """tls_ca takes PEM text, tls_ca_path a file path, used strictly as given."""
    cert_pem, _ = make_cert('localhost')
    ca_file = os.path.join(tmp.dir, 'ca.pem')
    with open(ca_file, 'w') as f:
        f.write(cert_pem)

    with SmtpTest() as app:
        # verify off keeps the smtplib default
        assert _create_tls_context(app, tls_verify_cert=False, tls_verify_hostname=False, tls_ca=cert_pem) is None
        # verify on without CA trusts the system CAs
        system = _create_tls_context(app)
        assert system.verify_mode == ssl.CERT_REQUIRED and system.check_hostname is True
        # the chain only, without the hostname
        chain = _create_tls_context(app, tls_verify_hostname=False)
        assert chain.verify_mode == ssl.CERT_REQUIRED and chain.check_hostname is False
        # tls_ca is PEM text, tls_ca_path a file path
        for options in (dict(tls_ca=cert_pem), dict(tls_ca_path=ca_file)):
            trusted_context = _create_tls_context(app, **options)
            assert [c['subject'] for c in trusted_context.get_ca_certs()] == [((('commonName', 'localhost'),),)]
        # the value is used strictly as given
        with pytest.raises(ssl.SSLError):
            _create_tls_context(app, tls_ca=ca_file)
        with pytest.raises(FileNotFoundError):
            _create_tls_context(app, tls_ca_path=os.path.join(tmp.dir, 'missing.pem'))


def test_create_tls_context_refuses_tls_ca_and_tls_ca_path(tmp):
    """tls_ca and tls_ca_path together raise a TokeoSmtpError, also on send."""
    cert_pem, _ = make_cert('localhost')
    with SmtpTest(tls_ca=cert_pem, tls_ca_path=os.path.join(tmp.dir, 'ca.pem')) as app:
        with pytest.raises(TokeoSmtpError, match='tls_ca and tls_ca_path are both set'):
            _create_tls_context(app, tls_ca=cert_pem, tls_ca_path=os.path.join(tmp.dir, 'ca.pem'))
        with mock.patch('smtplib.SMTP') as smtp:
            with pytest.raises(TokeoSmtpError, match='tls_ca and tls_ca_path are both set'):
                app.mail.send('body')
        # refused before connecting
        assert smtp.call_count == 0


def test_create_tls_context_refuses_tls_verify_hostname_without_tls_verify_cert():
    """tls_verify_hostname without tls_verify_cert raises, also on send."""
    with SmtpTest(tls_verify_hostname=True) as app:
        with pytest.raises(TokeoSmtpError, match='tls_verify_hostname needs tls_verify_cert'):
            _create_tls_context(app, tls_verify_cert=False, tls_verify_hostname=True)
        with mock.patch('smtplib.SMTP') as smtp:
            with pytest.raises(TokeoSmtpError, match='tls_verify_hostname needs tls_verify_cert'):
                app.mail.send('body')
        # refused before connecting
        assert smtp.call_count == 0


# ---- x-headers --------------------------------------------------------------


@pytest.mark.parametrize(
    'header_encoding, expected_encoded',
    [(None, '1'), ('base64', '=?utf-8?b?MQ==?='), ('qp', '=?utf-8?q?1?=')],
)
def test_x_headers(header_encoding, expected_encoded):
    """x_ arguments become X- headers, encoded like the subject."""
    with SmtpTest(header_encoding=header_encoding) as app:
        params = app.mail._get_params(X_API_Key='1', x_my_header='2')
        msg = app.mail._make_message('body', **params)

    # the name is kept as given, only the prefix separator becomes a dash;
    # with header_encoding the value is encoded like the subject
    assert 'X-my-header' in msg.keys()
    assert f'X-API-Key: {expected_encoded}\n' in msg.as_string()


# ---- attachments ------------------------------------------------------------


def test_attachment_names(tmp):
    """An attachment keeps a given name, else the name of its file."""
    pdf = os.path.join(tmp.dir, 'invoice.pdf')
    with open(pdf, 'wb') as f:
        f.write(b'%PDF-1.4 dummy')

    with SmtpTest() as app:
        files = [('docs/2026/q3.pdf', pdf), (None, pdf), {'path': pdf}]
        msg = app.mail._make_message('body', **app.mail._get_params(files=files))

    names = [part.get_filename() for _, part in _get_parts(msg) if part.get_filename()]
    # an alternative name is used as given, without one the file name applies
    assert names == ['docs/2026/q3.pdf', 'invoice.pdf', 'invoice.pdf']


def test_attachment_name_with_spaces_and_umlauts(tmp):
    """A name with spaces and umlauts arrives readable for the recipient."""
    pdf = os.path.join(tmp.dir, 'invoice.pdf')
    with open(pdf, 'wb') as f:
        f.write(b'%PDF-1.4 dummy')
    names = ['my report ä.pdf', 'my report.pdf']
    with SmtpTest() as app:
        raw = app.mail._make_message('body', **app.mail._get_params(files=[(name, pdf) for name in names])).as_string()
    # read by the classic parser of python and by the current one
    for policy in (email.policy.compat32, email.policy.default):
        parsed_message = email.message_from_string(raw, policy=policy)
        assert [part.get_filename() for part in parsed_message.walk() if part.get_filename()] == names


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
    assert image['Content-Disposition'] == 'inline; filename="logo.png"'
    # the image part is encoded once, with exactly one transfer encoding header
    assert image.get_all('Content-Transfer-Encoding') == ['base64']
    assert image.get_payload(decode=True) == PNG
    # without html the file is a plain attachment
    attachment = [p for _, p in _get_parts(text) if p.get_filename()][0]
    assert attachment.get_content_type() == 'application/octet-stream'
    assert attachment['Content-Disposition'] == 'attachment; filename="logo.png"'
    assert attachment['Content-ID'] is None


def test_inline_image_unsupported(tmp):
    """An svg as inline image raises TokeoSmtpError."""
    svg = os.path.join(tmp.dir, 'logo.svg')
    with open(svg, 'w') as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg"/>')

    with SmtpTest() as app:
        params = app.mail._get_params(files=[{'path': svg, 'cid': 'logo'}])
        with pytest.raises(TokeoSmtpError, match='Not a supported image: .*logo.svg at inline attachment'):
            app.mail._make_message(('text', '<img src="cid:logo">'), **params)


# ---- mime structure ---------------------------------------------------------


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


# ---- invalid body -----------------------------------------------------------


@pytest.mark.parametrize('item', ['to', 'cc', 'bcc'])
def test_recipients_as_str_raise(item):
    """to, cc or bcc as a str argument raises TypeError, no mail per char."""
    with SmtpTest() as app:
        with pytest.raises(TypeError, match=f"^{item} must be a list of addresses, not the str 'a@b.de'$"):
            app.mail._get_params(**{item: 'a@b.de'})
        with mock.patch('smtplib.SMTP') as smtp:
            with pytest.raises(TypeError, match=f'^{item} must be a list of addresses'):
                app.mail.send('body', **{item: 'a@b.de'})
        # refused before connecting
        assert smtp.call_count == 0


@pytest.mark.parametrize('item', ['to', 'cc', 'bcc'])
def test_recipients_as_str_of_config_become_lists(item):
    """to, cc or bcc as a str of the config is split by cement into a list."""
    with SmtpTest(**{item: 'a@b.de, c@d.de'}) as app:
        assert app.mail._get_params()[item] == ['a@b.de', 'c@d.de']


def test_tls_config_error_is_reported_by_main(monkeypatch, capsys):
    """main reports a wrong tls config by its message, exit 1, no traceback."""

    class SendingApp(SmtpTest):

        def __init__(self):
            # a config case: cement keeps both values as they are
            super().__init__(tls_ca='PEM', tls_ca_path='/ca.pem')

        def run(self):
            self.mail.send('body')

    monkeypatch.setattr(tokeo.main, 'Tokeo', SendingApp)
    with mock.patch('smtplib.SMTP') as smtp:
        with pytest.raises(SystemExit) as err_info:
            tokeo.main.main()
    assert err_info.value.code == 1
    assert smtp.call_count == 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert 'TokeoSmtpError:' in output
    assert 'tls_ca and tls_ca_path are both set' in output
    assert 'Traceback' not in output


@pytest.mark.parametrize('body', [5, {'text': None}, (5, '')], ids=['int', 'dict_none', 'tuple_int'])
def test_invalid_body(body):
    """An int or a None part as body raises TypeError."""
    with SmtpTest() as app:
        with pytest.raises(TypeError):
            app.mail._make_message(body, **app.mail._get_params())


# ---- date and message-id ----------------------------------------------------


def test_date_and_message_id():
    """Date and Message-Id are set, a given value wins, both can be left out."""
    with SmtpTest(msgid_domain='example.com') as app:
        enforced_message = app.mail._make_message('body', **app.mail._get_params())
        given_message = app.mail._make_message(
            'body', **app.mail._get_params(date='Mon, 01 Jan 2024 00:00:00 +0000', message_id='<given@example.com>')
        )
        absent = app.mail._make_message('body', **app.mail._get_params(date_enforce=False, msgid_enforce=False))

    assert enforced_message['Date'] and enforced_message['Message-Id'].endswith('@example.com>')
    # a given value wins over the generated one
    assert given_message['Date'] == 'Mon, 01 Jan 2024 00:00:00 +0000'
    assert given_message['Message-Id'] == '<given@example.com>'
    assert absent['Date'] is None and absent['Message-Id'] is None


# ---- send_by_template -------------------------------------------------------


def test_send_by_template_mail_params_wins_over_data():
    """A key mail_params in data is no error, the mail params win."""
    rendered_data = []
    with SmtpTest() as app:
        with mock.patch.object(app.template, 'load'), mock.patch.object(app.mail, 'send') as send:
            with mock.patch.object(app, 'render', side_effect=lambda data, *a, **kw: rendered_data.append(data) or 'body'):
                app.mail.send_by_template('mails/welcome', dict(user='tom', mail_params='x'), subject='Hi')
    assert rendered_data[0] == dict(user='tom', mail_params=dict(subject='Hi'))
    assert send.call_args.kwargs == dict(body=dict(text='body', html='body'), subject='Hi')


def test_send_by_template_escapes_only_the_html(tmp_path):
    """The html body escapes the values, subject and plain text keep them."""
    for part in ('title', 'plain', 'html'):
        (tmp_path / f'order.{part}.jinja2').write_text('{{ name }}')
    name = "O'Neil & <b>Söhne</b>"
    with SmtpTemplateTest() as app:
        app.add_template_dir(str(tmp_path))
        with mock.patch.object(app.mail, 'send') as send:
            app.mail.send_by_template('order', dict(name=name))
    html = 'O&#39;Neil &amp; &lt;b&gt;Söhne&lt;/b&gt;'
    assert send.call_args.kwargs == dict(body=dict(text=name, html=html), subject=name)
