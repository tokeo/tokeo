"""
Shared parts of the tests of tokeo.ext.smtpd: the test app with the smtpd
extension and an events handler that shows its options and process.
"""

import os
from tokeo.main import TokeoTest
from tokeo.ext.smtpd import TokeoSmtpdEvents


class SmtpdTest(TokeoTest):
    """A test app with the smtpd extension."""

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.print',
            'tokeo.ext.smtpd',
        ]


class TaggedSmtpdEvents(TokeoSmtpdEvents):
    """Answers EHLO with the tag of its options and the pid of its process."""

    def on_connect_event(self, ctx):
        super().on_connect_event(ctx)
        ctx.server.helo_response = f"{self.options.get('tag')} pid {os.getpid()}"
