import os  # noqa: F401
import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest

defaults = init_defaults('smtp')

subjMsg = 'Umlaute äöüß Kodiert'
textMsg = 'Body Text plain message [äöü]'
htmlMsg = '<html><body><h1 style="color: #aa0;">Welcome</h1><p>Hello world [äöü]!</p></body></html>'
htmlImg = '<html><body><h1 style="color: #aa0;">Welcome</h1><img src="cid:abcd1234.webp" /><p>Hello world [äöü]!</p></body></html>'

body = [textMsg, htmlMsg]


class SmtpTest(TokeoTest):

    class Meta:

        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.smtp',
        ]

        mail_handler = 'tokeo.smtp'


def test_smtp_qp_message(rando, tmp_path):
    # the message build needs no credentials and no network: embedded dummy
    # settings and generated attachment files keep this half runnable anywhere
    test_defaults = dict(smtp=dict(defaults['smtp']))
    test_defaults['smtp']['body_encoding'] = 'qp'
    test_defaults['smtp']['from_addr'] = 'Tester <tester@example.com>'
    test_defaults['smtp']['to'] = 'dev@example.com'

    pdf = tmp_path / 'invoice.pdf'
    pdf.write_bytes(b'%PDF-1.4 dummy invoice')
    webp = tmp_path / 'abcd1234.webp'
    webp.write_bytes(b'RIFF0000WEBPVP8 dummy')

    with SmtpTest(config_defaults=test_defaults) as app:
        mail = app.mail
        body = (textMsg, htmlImg)
        get_params = mail._get_params(
            to=mail._config('to'),
            subject=subjMsg,
            files=[
                ('Demo-Rechnung.pdf', str(pdf)),
                {'alt_name': 'abcd1234.webp', 'path': str(webp), 'cid': 'abcd1234.webp'},
            ],
        )
        msg = mail._make_message(body, **get_params).as_string()
        assert 'Content-Transfer-Encoding: quoted-printable' in msg
        # the umlauts of the plain body must arrive utf-8 quoted-printable
        assert '=C3=A4=C3=B6=C3=BC' in msg
        assert 'Demo-Rechnung.pdf' in msg and 'abcd1234.webp' in msg


def test_smtp_qp(rando):
    defaults['smtp']['body_encoding'] = 'qp'

    with SmtpTest(config_defaults=defaults) as app:

        # the send half needs real credentials from a local secret section
        if 'test_ext_smtp' not in app.config.get_sections():
            pytest.skip('no local test_ext_smtp section with smtp credentials configured')

        # merge the test specific section
        app.config.merge(dict(smtp=app.config.get_section_dict('test_ext_smtp')))

        mail = app.mail
        body = (textMsg, htmlImg)

        test_params = dict(
            to=app.mail._config('to'),
            subject=subjMsg,
            files=[
                ('Demo-Rechnung.pdf', './invoice.pdf'),
                {'alt_name': 'abcd1234.webp', 'path': './abcd1234.webp', 'cid': 'abcd1234.webp'},
            ],
        )

        test_params['x_super'] = 'Toller Key'
        test_params['x_binary'] = (12, 'hallo', -1)

        get_params = mail._get_params(**test_params)
        msg = mail._make_message(body, **get_params)
        app.print(msg.as_string())

        mail.send(body, **test_params)
