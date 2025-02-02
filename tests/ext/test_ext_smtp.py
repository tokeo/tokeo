import os
from tokeo.main import TestApp
from cement.utils.misc import init_defaults

defaults = init_defaults('smtp')
defaults['smtp']['from_addr'] = os.environ['TOKEO_SMTP_FROM_ADDR']
defaults['smtp']['host'] = os.environ['TOKEO_SMTP_HOST']
defaults['smtp']['port'] = 587
defaults['smtp']['timeout'] = 30
defaults['smtp']['tls'] = True
defaults['smtp']['auth'] = True
defaults['smtp']['username'] = os.environ['TOKEO_SMTP_USERNAME']
defaults['smtp']['password'] = os.environ['TOKEO_SMTP_PASSWORD']

textMsg = 'Body Text plain message [äöü]'
htmlMsg = '<html><body><h1 style="color: #aa0;">Welcome</h1><p>Hello world [äöü]!</p></body></html>'
htmlImg = '<html><body><h1 style="color: #aa0;">Welcome</h1><img src="cid:abcd1234.webp" /><p>Hello world [äöü]!</p></body></html>'

test_params = dict(
    to=[os.environ['TOKEO_SMTP_TO']],
    subject='Umlaute äöüß Kodiert',
)

body = [textMsg, htmlMsg]


class SMTPApp(TestApp):

    class Meta:
        label = 'tokeo_ext_smtp_test'
        extensions = ['tokeo.ext.smtp', 'tokeo.ext.print']
        mail_handler = 'tokeo.smtp'


def test_smtp_qp(rando):
    defaults['smtp']['body_encoding'] = 'qp'

    with SMTPApp(config_defaults=defaults) as app:

        app.run()

        mail = app.mail
        body = (textMsg, htmlImg)

        test_params['files'] = [
            ('Demo-Rechnung.pdf', './invoice.pdf'),
            {'alt_name': 'abcd1234.webp', 'path': './abcd1234.webp', 'cid': 'abcd1234.webp'},
        ]

        test_params['x_super'] = 'Toller Key'
        test_params['x_binary'] = (12, 'hallo', -1)

        get_params = mail._get_params(**test_params)
        msg = mail._make_message(body, **get_params)
        app.print(msg.as_string())

        mail.send(body, **test_params)
