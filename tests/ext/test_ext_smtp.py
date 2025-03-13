import os  # noqa: F401
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


def test_smtp_qp(rando):
    defaults['smtp']['body_encoding'] = 'qp'

    with SmtpTest(config_defaults=defaults) as app:

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
