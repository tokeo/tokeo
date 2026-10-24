"""
Shared parts of the tests of tokeo.ext.smtpd: the test app with the smtpd
extension.
"""

from tokeo.main import TokeoTest


class SmtpdTest(TokeoTest):
    """A test app with the smtpd extension."""

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.print',
            'tokeo.ext.smtpd',
        ]
