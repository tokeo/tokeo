"""
Shared parts of the tests of tokeo.ext.smtp: the test app, the messages and
the certificates.
"""

from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.utils.tls import create_certificate

_DEFAULTS = init_defaults('smtp')

SUBJECT = 'Umlaute äöüß Kodiert'
TEXT = 'Body Text plain message [äöü]'
HTML_WITH_IMAGE = '<html><body><h1 style="color: #aa0;">Welcome</h1><img src="cid:abcd1234.webp" /><p>Hello world [äöü]!</p></body></html>'


class SmtpTest(TokeoTest):
    """A test app whose smtp section is the defaults plus the given values."""

    class Meta:

        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.smtp',
        ]

        mail_handler = 'tokeo.smtp'

    def __init__(self, **smtp):
        section = dict(_DEFAULTS['smtp'])
        section.update(to=['to@example.com'], from_addr='from@example.com')
        section.update(smtp)
        super().__init__(config_defaults=dict(smtp=section))


class SmtpTemplateTest(SmtpTest):
    """A test app with tokeo.ext.smtp and tokeo.ext.jinja2, for mail templates."""

    class Meta:

        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.smtp',
        ]


def make_cert(*names):
    """
    Create a self-signed certificate valid as its own CA.

    ### Args

    - **names**: DNS names and IP addresses for the subject alt names

    ### Returns

    - **tuple**: ```(cert_pem, key_pem)``` as str

    """
    _, _, cert_pem, key_pem = create_certificate(names[0], sans=names, is_ca=True)
    return cert_pem.decode(), key_pem.decode()
