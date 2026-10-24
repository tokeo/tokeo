"""
Shared parts of the tests of tokeo.ext.smtp: the test app, the messages and
the certificates.
"""

import ipaddress
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest

DEFAULTS = init_defaults('smtp')

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
        section = dict(DEFAULTS['smtp'])
        section.update(to=['to@example.com'], from_addr='from@example.com')
        section.update(smtp)
        super().__init__(config_defaults=dict(smtp=section))


def make_cert(*names):
    """
    Create a self-signed certificate valid as its own CA.

    ### Args

    - **names**: DNS names and IP addresses for the subject alt names

    ### Returns

    - **tuple**: ```(cert_pem, key_pem)``` as str

    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    alt_names = []
    for name in names:
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alt_names.append(x509.DNSName(name))
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return cert_pem, key_pem
