"""
Tokeo smtp extension module.
"""

import os
from datetime import datetime, timezone
import smtplib
from email.header import Header
from email.charset import Charset, BASE64, QP
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email import encoders
from email.utils import format_datetime, make_msgid
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
            # define controlling of mail encoding
            'charset': 'utf-8',
            'header_encoding': None,
            'body_encoding': None,
            'date_enforce': True,
            'msgid_enforce': True,
            'msgid_str': None,
            'msgid_domain': 'localhost',
        }

    def __init__(self, **kw):
        super().__init__(**kw)
        self._template_exists_cache = dict()

    def _get_params(self, **kw):
        params = dict()

        # some keyword args override configuration defaults
        for item in [
            # fmt: off
            'to', 'from_addr', 'cc', 'bcc', 'subject', 'subject_prefix', 'files',
            'charset', 'header_encoding', 'body_encoding',
            'date_enforce', 'msgid_enforce', 'msgid_str',
            # fmt: on
        ]:
            config_item = self.app.config.get(self._meta.config_section, item)
            params[item] = kw.get(item, config_item)

        # others don't
        for item in [
            # fmt: off
            'ssl', 'tls', 'host', 'port', 'auth', 'username', 'password',
            'timeout', 'msgid_domain'
            # fmt: on
        ]:
            params[item] = self.app.config.get(self._meta.config_section, item)

        # some are only set by message
        for item in [
            # fmt: off
            'date', 'message_id', 'return_path', 'reply_to'
            # fmt: on
        ]:
            value = kw.get(item, None)
            if value is not None and str.strip(f'{value}') != '':
                params[item] = kw.get(item, config_item)

        # take all X-headers as is
        for item in kw.keys():
            if len(item) > 2 and item.startswith(('x-', 'X-', 'x_', 'X_')):
                value = kw.get(item, None)
                if value is not None:
                    params[f'X-{item[2:]}'] = value

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
            self.app.log.debug(f'{self._meta.label} : initiating smtp over ssl')

        else:
            server = smtplib.SMTP(params['host'], params['port'], params['timeout'])
            self.app.log.debug(f'{self._meta.label} : initiating smtp')

        if self.app.debug is True:
            server.set_debuglevel(9)

        if is_true(params['tls']):
            self.app.log.debug(f'{self._meta.label} : initiating tls')
            server.starttls()

        if is_true(params['auth']):
            server.login(params['username'], params['password'])

        msg = self._make_message(body, **params)
        server.send_message(msg)

        server.quit()

    def _header(self, value, _charset=None, **params):
        return Header(value, charset=_charset) if params['header_encoding'] else value

    def _make_message(self, body, **params):
        # use encoding for header parts
        cs_header = Charset(params['charset'])
        if params['header_encoding'] == 'base64':
            cs_header.header_encoding = BASE64
        elif params['header_encoding'] == 'qp' or params['body_encoding'] == 'quoted-printable':
            cs_header.header_encoding = QP

        # use encoding for body parts
        cs_body = Charset(params['charset'])
        if params['body_encoding'] == 'base64':
            cs_body.body_encoding = BASE64
        elif params['body_encoding'] == 'qp' or params['body_encoding'] == 'quoted-printable':
            cs_body.body_encoding = QP

        # setup body parts
        partText = None
        partHtml = None

        # check the body argument
        if type(body) not in [str, tuple, dict]:
            error_msg = (
                # fmt: off
                "Message body must be string, tuple "
                "('<text>', '<html>') or dict "
                "{'text': '<text>', 'html': '<html>'}"
                # fmt: on
            )
            raise TypeError(error_msg)

        # get the body parts
        if isinstance(body, str):
            partText = MIMEText(body, 'plain', _charset=cs_body)
        elif isinstance(body, tuple):
            # handle plain text
            if len(body) >= 1 and body[0] and str.strip(body[0]) != '':
                partText = MIMEText(str.strip(body[0]), 'plain', _charset=cs_body)
            # handle html
            if len(body) >= 2 and body[1] and str.strip(body[1]) != '':
                partHtml = MIMEText(str.strip(body[1]), 'html', _charset=cs_body)
        elif isinstance(body, dict):
            # handle plain text
            if 'text' in body and str.strip(body['text']) != '':
                partText = MIMEText(str.strip(body['text']), 'plain', _charset=cs_body)
            # handle html
            if 'html' in body and str.strip(body['html']) != '':
                partHtml = MIMEText(str.strip(body['html']), 'html', _charset=cs_body)

        # To define the correct message content-type
        # we need to indentify the content of this mail.
        # If only "text" exists => text/plain, if only
        # "html" exists => text/html, if "text" and
        # "html" exists => multipart/alternative. In
        # any case that files exists => multipart/mixed.
        # Set message charset and encoding based on parts
        if params['files']:
            msg = MIMEMultipart('mixed')
            msg.set_charset(params['charset'])
        elif partText and partHtml:
            msg = MIMEMultipart('alternative')
            msg.set_charset(params['charset'])
        elif partHtml:
            msg = MIMEBase('text', 'html')
            msg.set_charset(cs_body)
        else:
            msg = MIMEBase('text', 'plain')
            msg.set_charset(cs_body)

        # create message
        msg['From'] = params['from_addr']
        msg['To'] = ', '.join(params['to'])
        if params['cc']:
            msg['Cc'] = ', '.join(params['cc'])
        if params['bcc']:
            msg['Bcc'] = ', '.join(params['bcc'])
        if params['subject_prefix'] not in [None, '']:
            msg['Subject'] = self._header(f'{params['subject_prefix']} {params['subject']}', _charset=cs_header, **params)
        else:
            msg['Subject'] = self._header(params['subject'], _charset=cs_header, **params)
        # check for date
        if is_true(params['date_enforce']) and not params.get('date', None):
            params['date'] = format_datetime(datetime.now(timezone.utc))
        # check for message-id
        if is_true(params['msgid_enforce']) and not params.get('message_id', None):
            params['message_id'] = make_msgid(params['msgid_str'], params['msgid_domain'])

        # check for message headers
        if params.get('date', None):
            msg['Date'] = params['date']
        if params.get('message_id', None):
            msg['Message-Id'] = params['message_id']
        if params.get('return_path', None):
            msg['Return-Path'] = params['return_path']
        if params.get('reply_to', None):
            msg['Reply-To'] = params['reply_to']

        # check for X-headers
        for item in params.keys():
            if item.startswith('X-'):
                msg.add_header(item.title(), self._header(f'{params[item]}', _charset=cs_header, **params))

        # append the body parts
        if params['files']:
            # multipart/mixed
            if partHtml:
                # when html exists, create always a related part to include
                # the body alternatives and eventually files as related
                # attachments (e.g. images).
                rel = MIMEMultipart('related')
                # create an alternative part to include bodies for text and html
                alt = MIMEMultipart('alternative')
                # body text and body html
                if partText:
                    alt.attach(partText)
                alt.attach(partHtml)
                rel.attach(alt)
                msg.attach(rel)
            else:
                # only body text or no body
                if partText:
                    msg.attach(partText)
                else:
                    # no body no files = empty message = just headers
                    pass
        else:
            # multipart/alternative
            if partText and partHtml:
                # plain/text and plain/html
                msg.attach(partText)
                msg.attach(partHtml)
            else:
                # plain/text or plain/html only so just append payload
                if partText:
                    msg.set_payload(partText.get_payload(), charset=cs_body)
                elif partHtml:
                    msg.set_payload(partHtml.get_payload(), charset=cs_body)
                else:
                    # no body no files = empty message = just headers
                    pass

        # attach files
        if params['files']:
            for in_path in params['files']:
                # support for alternative file name if its tuple or dict
                # like [
                #     'path/simple.ext',
                #     ('alt_name.ext', 'path/filename.ext'),
                #     ('alt_name.ext', 'path/filename.ext', 'cidname'),
                #     {
                #         'name': 'alt_name',
                #         'path': 'path/filename.ext',
                #         cid: 'cidname'
                #     },
                # ]
                if isinstance(in_path, tuple):
                    alt_name = in_path[0]
                    path = in_path[1]
                    cid = in_path[2] if len(in_path) >= 3 else None
                elif isinstance(in_path, dict):
                    alt_name = in_path.get('name', None)
                    path = in_path.get('path')
                    cid = in_path.get('cid', None)
                else:
                    alt_name = None
                    path = in_path
                    cid = None

                path = fs.abspath(path)
                if not alt_name:
                    alt_name = os.path.basename(path)

                # add attachment payload from file
                with open(path, 'rb') as file:
                    # check for embedded image or regular attachments
                    if cid:
                        part = MIMEImage(file.read())
                    else:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(file.read())

                # encoder
                encoders.encode_base64(part)

                # embedded inline or attachment
                if cid:
                    # inline alt_name and id header
                    part.add_header(
                        'Content-Disposition',
                        f'inline; filename={alt_name}',
                    )
                    part.add_header('Content-ID', f'<{cid}>')
                    rel.attach(part)
                else:
                    # attachment alt_name header
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename={alt_name}',
                    )
                    msg.attach(part)

        return msg

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
            except Exception:
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
        body = dict()
        if _template_exists(f'{template}.plain.jinja2'):
            body['text'] = self.app.render(dict(**data, mail_params=params), f'{template}.plain.jinja2', out=None)
        if _template_exists(f'{template}.html.jinja2'):
            body['html'] = self.app.render(dict(**data, mail_params=params), f'{template}.html.jinja2', out=None)
        # send the message
        self.send(body=body, **params)


def load(app):
    app.handler.register(TokeoSMTPMailHandler)
    app._meta.mail_handler = TokeoSMTPMailHandler.Meta.label
