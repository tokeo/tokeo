"""
Tokeo SMTP extension module.

This extension provides a robust email sending capability via SMTP protocol
for Tokeo applications. It supports plain text and HTML email messages,
file attachments, inline images, template-based emails, and various
encoding options.

The module implements a mail handler that is fully compliant with the
Cement framework mail interface, while extending it with additional
features like template-based email generation and comprehensive
configuration options.

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
    SMTP mail handler for sending emails in Tokeo applications.

    This class implements the Cement framework's mail interface and extends it with
    additional features for comprehensive email communication. The implementation
    is based on the Python standard library's `smtplib` module.

    The handler supports various email types including plain text, HTML,
    multipart messages with attachments, and inline images. It also provides
    template-based email generation using Tokeo's and Jinja's templating system.

    ### Notes:

    : This handler requires proper SMTP server configuration in the application
        configuration. The configuration can be overridden per message via keyword
        arguments when sending emails.

    ### See Also:

    1. Python smtplib documentation: https://docs.python.org/library/smtplib.html
    1. Email composition guide: https://mailtrap.io/blog/python-send-html-email/

    """

    class Meta:
        """
        Handler meta-data defining configuration and behavior.

        ### Notes:

        : This class defines the configuration section, default values,
            and other metadata required by the Cement framework for
            proper handler registration and operation.

        """

        #: Unique identifier for this handler
        label = 'tokeo.smtp'

        #: Configuration section name in the application config
        config_section = 'smtp'

        #: Configuration default values
        config_defaults = {
            # Email recipients and sender
            'to': [],  # List of primary recipients
            'from_addr': 'noreply@localhost',  # Sender email address
            'cc': [],  # Carbon copy recipients
            'bcc': [],  # Blind carbon copy recipients
            # Email content configuration
            'subject': None,  # Email subject line
            'subject_prefix': None,  # Optional prefix for all subjects
            'files': None,  # Attachments or inline images
            # SMTP server configuration
            'host': 'localhost',  # SMTP server hostname
            'port': '25',  # SMTP server port
            'timeout': 30,  # Connection timeout in seconds
            'ssl': False,  # Use SSL/TLS connection
            'tls': False,  # Use STARTTLS command
            'auth': False,  # Use SMTP authentication
            'username': None,  # SMTP username
            'password': None,  # SMTP password
            # Email encoding options
            'charset': 'utf-8',  # Character set for email
            'header_encoding': None,  # Encoding for headers (None, 'base64', 'qp')
            'body_encoding': None,  # Encoding for body (None, 'base64', 'qp')
            # Message identification
            'date_enforce': True,  # Auto-add Date header if missing
            'msgid_enforce': True,  # Auto-add Message-ID if missing
            'msgid_str': None,  # Custom string for Message-ID generation
            'msgid_domain': 'localhost',  # Domain for Message-ID generation
        }

    def __init__(self, **kw):
        """
        Initialize the SMTP mail handler.

        Sets up the handler and initializes the template cache.

        ### Args:

        - **kw**: Keyword arguments passed to the parent handler

        ### Notes:

        : Initializes an internal cache to optimize template existence checks
            during template-based email generation.

        """
        super().__init__(**kw)
        self._template_exists_cache = dict()

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a simple wrapper around the application's config.get method
        that automatically uses the correct configuration section.

        ### Args:

        - **key** (str): Configuration key to retrieve
        - **kwargs**: Additional arguments passed to config.get()

        ### Returns:

        - Configuration value for the specified key

        ### Example:

        ```python
        # Get the SMTP host from config
        host = handler._config('host')

        # Get a value with a fallback default
        port = handler._config('port', fallback='587')
        ```

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def _get_params(self, **kw):
        """
        Merge configuration and keyword parameters for email sending.

        This method handles the merging of configuration defaults with
        per-message overrides specified in keyword arguments. It also
        processes special parameters like X-headers.

        ### Args:

        - **kw**: Keyword arguments for message-specific parameter overrides

        ### Returns:

        - **dict**: Merged parameters for email creation

        ### Notes:

        : Parameter merging follows specific rules: message parameters like 'to'
            and 'subject' can be overridden by keyword arguments, while connection
            parameters like 'host' and 'port' are always taken from configuration.
            Special headers like Date and X-headers receive special handling.

        ### Example:

        ```python
        # Get parameters with some overrides
        params = handler._get_params(
            to=['user@example.com'],
            subject='Important message',
            x_priority='High'
        )
        ```

        """
        params = dict()

        # Parameters that can be overridden by keyword arguments
        for item in [
            # fmt: off
            'to', 'from_addr', 'cc', 'bcc', 'subject', 'subject_prefix', 'files',
            'charset', 'header_encoding', 'body_encoding',
            'date_enforce', 'msgid_enforce', 'msgid_str',
            # fmt: on
        ]:
            config_item = self.app.config.get(self._meta.config_section, item)
            params[item] = kw.get(item, config_item)

        # Connection parameters always come from configuration
        for item in [
            # fmt: off
            'ssl', 'tls', 'host', 'port', 'auth', 'username', 'password',
            'timeout', 'msgid_domain'
            # fmt: on
        ]:
            params[item] = self.app.config.get(self._meta.config_section, item)

        # Message headers that are only set explicitly, not from config
        for item in [
            # fmt: off
            'date', 'message_id', 'return_path', 'reply_to'
            # fmt: on
        ]:
            value = kw.get(item, None)
            if value is not None and str.strip(f'{value}') != '':
                params[item] = kw.get(item, config_item)

        # Process all X-headers from keyword arguments
        for item in kw.keys():
            if len(item) > 2 and item.startswith(('x-', 'X-', 'x_', 'X_')):
                value = kw.get(item, None)
                if value is not None:
                    params[f'X-{item[2:]}'] = value

        return params

    def send(self, body, **kw):
        """
        Send an email message via SMTP.

        Sends email messages with support for plain text, HTML, attachments,
        and other email features. The method handles various email body formats
        and establishes the appropriate SMTP connection based on configuration.

        ### Args:

        - **body** (str|tuple|dict): The message body content to send. Can be:

            - A string for plain text email
            - A tuple of (text, html) for multipart emails
            - A dict with 'text' and/or 'html' keys

        ### Keyword Args:

        - **to** (list): List of primary recipient email addresses
        - **from_addr** (str): Email address of the sender
        - **cc** (list): List of carbon copy recipient email addresses
        - **bcc** (list): List of blind carbon copy recipient email addresses
        - **subject** (str): Email subject line
        - **subject_prefix** (str): Prefix for the subject line
        - **files** (list): List of file paths to attach to the message
        - **header_encoding** (str): Encoding for email headers ('base64' or 'qp')
        - **body_encoding** (str): Encoding for email body ('base64' or 'qp')
        - **date** (str): Custom date for the Date header
        - **message_id** (str): Custom Message-ID header value
        - **return_path** (str): Return-Path header value
        - **reply_to** (str): Reply-To header value
        - **x_***: Any parameter starting with 'x_' will be added as an X-header

        ### Returns:

        - **bool**: `True` if message is sent successfully, `False` otherwise

        ### Raises:

        - **TypeError**: If the body parameter is not a string, tuple, or dict
        - **smtplib.SMTPException**: If SMTP server connection or sending fails

        ### Example:

        ```python
        # Basic text email with configuration defaults
        app.mail.send('This is my message body')

        # Text and HTML multipart email with custom settings
        app.mail.send(
            (
              'Plain text version',
              '<html><body><h1>HTML version</h1></body></html>',
            ),
            from_addr='sender@example.com',
            to=['recipient@example.com'],
            cc=['cc@example.com'],
            subject='Important notification',
            files=['/path/to/attachment.pdf']
        )

        # Using a dictionary for message body with custom headers
        app.mail.send(
            {'text': 'Text version', 'html': '<p>HTML version</p>'},
            subject='System alert',
            x_priority='1',
            return_path='bounces@example.com'
        )
        ```

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
        """
        Create an email header value with proper encoding if needed.

        ### Args:

        - **value** (str): The header value to encode
        - **_charset** (Charset): Character set for encoding
        - **params**: Parameter dictionary containing encoding settings

        ### Returns:

        - **str|Header**: Encoded Header object or plain string

        ### Notes:

        : Returns a Header object with proper encoding if header_encoding is
            specified in the params, otherwise returns the value as-is.

        """
        return Header(value, charset=_charset) if params['header_encoding'] else value

    def _make_message(self, body, **params):
        """
        Create an email message object with all necessary parts.

        Constructs a complete email message with appropriate content types,
        encodings, body parts, attachments, and headers based on the provided
        parameters.

        ### Args:

        - **body** (str|tuple|dict): The message body content in one of
            supported formats
        - **params**: Dictionary of email parameters from _get_params()

        ### Returns:

        - **MIMEBase**: Complete email message ready for sending

        ### Raises:

        - **TypeError**: If body has an unsupported type

        ### Notes:

        : This method handles all the complexity of creating multipart messages,
            setting proper encodings, and handling attachments. The message structure
            will vary depending on the content:

            1. text/plain: For text-only emails
            1. text/html: For HTML-only emails
            1. multipart/alternative: For emails with both text and HTML versions
            1. multipart/mixed: For emails with attachments
            1. multipart/related: For HTML emails with inline images

        """
        # Set up encoding for header parts
        cs_header = Charset(params['charset'])
        if params['header_encoding'] == 'base64':
            cs_header.header_encoding = BASE64
        elif params['header_encoding'] == 'qp' or params['body_encoding'] == 'quoted-printable':
            cs_header.header_encoding = QP

        # Set up encoding for body parts
        cs_body = Charset(params['charset'])
        if params['body_encoding'] == 'base64':
            cs_body.body_encoding = BASE64
        elif params['body_encoding'] == 'qp' or params['body_encoding'] == 'quoted-printable':
            cs_body.body_encoding = QP

        # Initialize body part containers
        partText = None
        partHtml = None

        # Validate and process the body argument
        if type(body) not in [str, tuple, dict]:
            error_msg = (
                # fmt: off
                "Message body must be string, tuple "
                "('<text>', '<html>') or dict "
                "{'text': '<text>', 'html': '<html>'}"
                # fmt: on
            )
            raise TypeError(error_msg)

        # Extract text and HTML parts from the body argument
        if isinstance(body, str):
            # String body = plain text only
            partText = MIMEText(body, 'plain', _charset=cs_body)
        elif isinstance(body, tuple):
            # Tuple body = (text, html)
            # Process plain text part if provided
            if len(body) >= 1 and body[0] and str.strip(body[0]) != '':
                partText = MIMEText(str.strip(body[0]), 'plain', _charset=cs_body)
            # Process HTML part if provided
            if len(body) >= 2 and body[1] and str.strip(body[1]) != '':
                partHtml = MIMEText(str.strip(body[1]), 'html', _charset=cs_body)
        elif isinstance(body, dict):
            # Dict body = {'text': '...', 'html': '...'}
            # Process plain text part if provided
            if 'text' in body and str.strip(body['text']) != '':
                partText = MIMEText(str.strip(body['text']), 'plain', _charset=cs_body)
            # Process HTML part if provided
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
        """
        Send an email using a template for content generation.

        This method provides template-based email sending by rendering email
        content from Jinja2 templates. It looks for template files with specific
        naming conventions to generate the subject, plain text, and HTML parts
        of the email.

        ### Args:

        - **template** (str): Base template path/name without extension
        - **data** (dict): Data context for template rendering
        - **kw**: Additional email parameters passed to send()

        ### Returns:

        - **bool**: Result from the send() method

        ### Notes:

        : The method looks for the following template files:

            1. `{template}.title.jinja2`: For subject line (optional)
            1. `{template}.plain.jinja2`: For plain text body (optional)
            1. `{template}.html.jinja2`: For HTML body (optional)

        : At least one of plain text or HTML templates should exist.
            The mail_params context variable is provided to templates
            containing the email parameters.

        ### Example:

        ```python
        # Assuming templates/emails/welcome.plain.jinja2,
        # templates/emails/welcome.html.jinja2, and
        # templates/emails/welcome.title.jinja2 exist:

        app.mail.send_by_template(
            'emails/welcome',
            {'username': 'john', 'account_info': account_data},
            to=['john@example.com'],
            files=['/path/to/terms.pdf']
        )
        ```

        """

        # Helper function to check if a template exists and cache the result
        def _template_exists(template):
            """
            Check if a template exists and cache the result.

            Attempts to load the template and caches the result to avoid
            repeated file system or module lookups for the same template.

            ### Args:

            - **template** (str): Template path to check

            ### Returns:

            - **bool**: True if template exists and can be loaded

            """
            # Check if stored in cache already
            if template in self._template_exists_cache:
                return self._template_exists_cache[template]

            # Do first time check from file or module
            result = False
            try:
                # Successfully load when available
                self.app.template.load(template)
                result = True
            except Exception:
                pass

            # Store flag in cache to prevent repeated lookups
            self._template_exists_cache[template] = result
            return result

        # Prepare email params
        params = dict(**kw)

        # Check if we need to render the subject from template
        if 'subject' not in params:
            if _template_exists(f'{template}.title.jinja2'):
                params['subject'] = self.app.render(data, f'{template}.title.jinja2', out=None)

        # Build the email body from templates
        body = dict()

        # Plain text template
        if _template_exists(f'{template}.plain.jinja2'):
            body['text'] = self.app.render(dict(**data, mail_params=params), f'{template}.plain.jinja2', out=None)
        if _template_exists(f'{template}.html.jinja2'):
            body['html'] = self.app.render(dict(**data, mail_params=params), f'{template}.html.jinja2', out=None)
        # send the message
        self.send(body=body, **params)


def load(app):
    """
    Load the TokeoSMTPMailHandler and register it with the application.

    This function is called by the Cement framework when the extension
    is loaded. It registers the mail handler and sets it as the default
    mail handler for the application.

    ### Args:

    - **app**: The application instance

    ### Example:

    ```python
    # In your application configuration:
    class MyApp(App):
        class Meta:
            extensions = [
                'tokeo.ext.smtp',
            ]

            # Set as the default mail handler
            mail_handler = 'tokeo.smtp'
    ```

    """
    app.handler.register(TokeoSMTPMailHandler)
    app._meta.mail_handler = TokeoSMTPMailHandler.Meta.label
