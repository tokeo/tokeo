"""
Example smtpd events handler for the {{ app_name }} application.

Serves the mx-{{ app_label }} service from config/smtpd.yaml: accepts mail
from tokeo@local to tokeo@local, reads the received message and looks for an
url in the body text -- when one is found the count_words task is dispatched
for it.
"""

import re
from tokeo.ext.smtpd import TokeoSmtpdEvents
from tokeo.core.smtpd.exc import Smtpd550Exception
from {{ app_label }}.core import tasks


#: a simple url matcher for the example (http/https up to the next whitespace)
URL_PATTERN = re.compile(r'https?://\S+')


class {{ app_class_name }}SmtpdEvents(TokeoSmtpdEvents):
    """
    Events handler for the mx-{{ app_label }} receiving service.

    Subclasses ```TokeoSmtpdEvents``` and therefore reaches every app
    service via ```self.app``` from any event.

    """

    def on_mail_from_event(self, ctx, mail_from_data):
        self.app.log.info(f'mx-{{ app_label }}: mail from {mail_from_data}')
        # cleanup the address
        address = re.sub(r'^\s*<\s*(.*)\s*>\s*$', r'\1', mail_from_data).lower()
        # accept mail from the local tokeo sender only
        if address != 'tokeo@local':
            raise Smtpd550Exception('sender not allowed')
        # push address on ctx
        return address

    def on_rcpt_to_event(self, ctx, rcpt_to_data):
        self.app.log.info(f'mx-{{ app_label }}: rcpt to {rcpt_to_data}')
        # cleanup the address
        address = re.sub(r'^\s*<\s*(.*)\s*>\s*$', r'\1', rcpt_to_data).lower()
        # accept mail to the local tokeo recipient only
        if address != 'tokeo@local':
            raise Smtpd550Exception('recipient not allowed')
        # push address on ctx
        return address

    def on_message_data_event(self, ctx):
        # the whole message is in memory (no spool configured); a simple
        # regex lookup finds the first url in the message text
        match = URL_PATTERN.search(ctx.message.data.decode('utf-8', errors='replace'))
        if match:
            url = match.group(0)
            self.app.log.info(f'mx-{{ app_label }}: enqueue tasks count_words for {url}')
            tasks.actors.count_words.send(url)
