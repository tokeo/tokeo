from cement.utils.test import TestApp
from cement.utils.misc import init_defaults

defaults = init_defaults('smtp')
defaults['smtp']['from_addr'] = 'hallo@wechseljahre-workbook.de'
defaults['smtp']['host'] = 'mail.routing.net'
defaults['smtp']['port'] = 587
defaults['smtp']['timeout'] = 30
defaults['smtp']['tls'] = True
defaults['smtp']['auth'] = True
defaults['smtp']['username'] = 'hallo@wechseljahre-workbook.de'
defaults['smtp']['password'] = '[[a;vB.FO+py[cxIjRKZiF9aHYi$=gT!X;o+=5~6'

textMsg = 'Body Text plain message [äöü]'
htmlMsg = '<html><body><h1 style="color: #aa0;">Welcome</h1><p>Hello world [äöü]!</p></body></html>'
htmlImg = '<html><body><h1 style="color: #aa0;">Welcome</h1><img src="cid:wjwb.webp" /><p>Hello world [äöü]!</p></body></html>'

test_params = dict(
    to=['thfreudenberg@gmail.com'],
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
            ('Demo-Rechnung.pdf', '/Users/thfreudenberg/Downloads/PROFORMA-RE-N25A-2CWB.pdf'),
            {'alt_name': 'wjwb.webp', 'path': '/Users/thfreudenberg/Downloads/buch-cover.webp', 'cid': 'wjwb.webp'},
        ]

        test_params['x_super'] = 'Toller Key'
        test_params['x_binary'] = (12, 'hallo', -1)

        get_params = mail._get_params(**test_params)
        msg = mail._make_message(body, **get_params)
        app.print(msg.as_string())

        mail.send(body, **test_params)
