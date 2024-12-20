"""
Tokeo smtp extension module.
"""

import os
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from cement.core import mail
from cement.utils import fs
from cement.utils.misc import is_true


class TokeoSMTPMailHandler(mail.MailHandler):
    """
    This class implements the :ref:`IMail <cement.core.mail>`
    interface, and is based on the `smtplib
    <http://docs.python.org/dev/library/smtplib.html>`_ standard library.

    A complete documentation about python and mail could be find at:
    https://mailtrap.io/blog/python-send-html-email/

    """

    class Meta:
        """Handler meta-data."""

        #: Unique identifier for this handler
        label = 'tokeo.smtp'

        #: Id for config
        config_section = 'smtp'

        #: Configuration default values
        config_defaults = {
            'to': [],
            'from_addr': 'noreply@localhost',
            'cc': [],
            'bcc': [],
            'subject': None,
            'subject_prefix': None,
            'host': 'localhost',
            'port': '25',
            'timeout': 30,
            'ssl': False,
            'tls': False,
            'auth': False,
            'username': None,
            'password': None,
            'files': None,
        }

    def __init__(self, **kw):
        super().__init__(**kw)
        self._template_exists_cache = dict()

    def _get_params(self, **kw):
        params = dict()

        # some keyword args override configuration defaults
        for item in ['to', 'from_addr', 'cc', 'bcc', 'subject', 'subject_prefix', 'files']:
            config_item = self.app.config.get(self._meta.config_section, item)
            params[item] = kw.get(item, config_item)

        # others don't
        other_params = ['ssl', 'tls', 'host', 'port', 'auth', 'username', 'password', 'timeout']
        for item in other_params:
            params[item] = self.app.config.get(self._meta.config_section, item)

        return params

    def send(self, body, **kw):
        """
        Send an email message via SMTP.  Keyword arguments override
        configuration defaults (cc, bcc, etc).

        Args:
            body (list): The message body to send. List is treated as:
                ``[<text>, <html>]``. If a single string is passed it will be
                converted to ``[<text>]``. At minimum, a text version is
                required.

        Keyword Args:
            to (list): List of recipients (generally email addresses)
            from_addr (str): Address (generally email) of the sender
            cc (list): List of CC Recipients
            bcc (list): List of BCC Recipients
            subject (str): Message subject line
            subject_prefix (str): Prefix for message subject line (useful to
                override if you want to remove/change the default prefix).
            files (list): List of file paths to attach to the message.

        Returns:
            bool:``True`` if message is sent successfully, ``False`` otherwise

        Example:

            .. code-block:: python

                # Using all configuration defaults
                app.mail.send('This is my message body')

                # Overriding configuration defaults
                app.mail.send('My message body'
                    from_addr='me@example.com',
                    to=['john@example.com'],
                    cc=['jane@example.com', 'rita@example.com'],
                    subject='This is my subject',
                    )

        """
        params = self._get_params(**kw)

        if is_true(params['ssl']):
            server = smtplib.SMTP_SSL(params['host'], params['port'], params['timeout'])
            self.app.log.debug('%s : initiating smtp over ssl' % self._meta.label)

        else:
            server = smtplib.SMTP(params['host'], params['port'], params['timeout'])
            self.app.log.debug('%s : initiating smtp' % self._meta.label)

        if self.app.debug is True:
            server.set_debuglevel(9)

        if is_true(params['tls']):
            self.app.log.debug('%s : initiating tls' % self._meta.label)
            server.starttls()

        if is_true(params['auth']):
            server.login(params['username'], params['password'])

        self._send_message(server, body, **params)
        server.quit()

    def _send_message(self, server, body, **params):
        msg = MIMEMultipart('alternative')
        msg.set_charset('utf-8')

        msg['From'] = params['from_addr']
        msg['To'] = ', '.join(params['to'])
        if params['cc']:
            msg['Cc'] = ', '.join(params['cc'])
        if params['bcc']:
            msg['Bcc'] = ', '.join(params['bcc'])
        if params['subject_prefix'] not in [None, '']:
            subject = '%s %s' % (params['subject_prefix'], params['subject'])
        else:
            subject = params['subject']
        msg['Subject'] = Header(subject)

        # add body as text and or or as html
        partText = None
        partHtml = None
        if isinstance(body, str):
            partText = MIMEText(body)
        elif isinstance(body, list):
            # handle plain text
            if len(body) >= 1:
                partText = MIMEText(body[0], 'plain')

            # handle html
            if len(body) >= 2:
                partHtml = MIMEText(body[1], 'html')

        if partText:
            msg.attach(partText)
        if partHtml:
            msg.attach(partHtml)

        # attach files
        if params['files']:
            for in_path in params['files']:
                part = MIMEBase('application', 'octet-stream')

                # support for alternative file name if its tuple
                # like ['filename.ext', 'attname.ext']
                if isinstance(in_path, tuple):
                    attname = in_path[0]
                    path = in_path[1]
                else:
                    attname = os.path.basename(in_path)
                    path = in_path

                path = fs.abspath(path)

                # add attachment
                with open(path, 'rb') as file:
                    part.set_payload(file.read())

                # encode and name
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename={attname}',
                )
                msg.attach(part)

        server.send_message(msg)

    def send_by_template(self, template, data={}, **kw):
        # test if template exists by loading it
        def _template_exists(template):
            # check if stored in cache already
            if template in self._template_exists_cache:
                return self._template_exists_cache[template]
            # do first time check from file or module
            result = False
            try:
                # successfully load when available
                self.app.template.load(template)
                result = True
            except:
                pass
            # store flag in cache list to prevent often load access
            self._template_exists_cache[template] = result
            # return state
            return result

        # prepare email params
        params = dict(**kw)
        # check render subject
        if 'subject' not in params:
            if _template_exists(f'{template}.title.jinja2'):
                params['subject'] = self.app.render(data, f'{template}.title.jinja2', out=None)
        # build body
        body = list()
        if _template_exists(f'{template}.plain.jinja2'):
            body.append(self.app.render(dict(**data, mail_params=params), f'{template}.plain.jinja2', out=None))
        if _template_exists(f'{template}.html.jinja2'):
            # before adding a html part make sure that plain part exists
            if len(body) == 0:
                body.append('Content is delivered as HTML only.')
            body.append(self.app.render(dict(**data, mail_params=params), f'{template}.html.jinja2', out=None))
        # send the message
        self.send(body=body, **params)


def load(app):
    app._meta.mail_handler = TokeoSMTPMailHandler.Meta.label
    app.handler.register(TokeoSMTPMailHandler)
