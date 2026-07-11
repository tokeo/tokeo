"""
Tokeo SMTPD Exceptions Module.

This module defines the exception model of the SMTPD extension. It mirrors the
```midi-smtp-server``` exception hierarchy 1:1, so a ported handler raises the
very same names (```Smtpd550Exception```, ```Smtpd421Exception```, ...) it did
in Ruby. The server is the single place that catches these
and translates them into aiosmtpd responses; the handler logic never returns
raw SMTP codes.

### Features

- Extension-level errors (```SmtpdError```, ```SmtpdContractError```) for
    config, wiring, and contract violations, rooted in ```TokeoError```
- Internal control-flow signals (```SmtpdStopConnectionException``` and
    friends) that carry no SMTP code, matching midi's internal exceptions
- SMTP dialog exceptions (```SmtpdException``` and the numeric subclasses)
    that carry a fixed code and text; ```smtp_response``` renders the wire form
- Faithful codes and default texts copied verbatim from midi-smtp-server

"""

from tokeo.core.exc import TokeoError


class SmtpdError(TokeoError):
    """
    Base error for the SMTPD extension itself (config and wiring).

    ### Notes

    : Inherits from TokeoError to keep consistent error handling across Tokeo

    : Raised by the extension, not by handler logic; handler logic raises the
        SMTP dialog exceptions below instead

    """

    pass


class SmtpdContractError(SmtpdError):
    """
    Raised when a handler violates the events contract.

    ### Notes

    : Fired at import time by the ```@threaded``` decorator and by
        ```SmtpdEvents.__init_subclass__```, so a mis-wired handler fails before
        any service starts, never silently at runtime

    """

    pass


# --- internal control-flow signals (no SMTP code, like midi's internals) ---


class SmtpdSignal(Exception):
    """
    Base for internal control-flow signals without an SMTP return code.

    ### Notes

    : These mirror midi's internal RuntimeError signals; they steer the server
        (stop a connection or the service, abort a slow or oversized line) and
        are not translated into a client response like ```SmtpdException```

    """

    pass


class SmtpdStopConnectionException(SmtpdSignal):
    """Signal to actively close the current client connection."""

    pass


class SmtpdStopServiceException(SmtpdSignal):
    """Signal to stop the service without a fatal error message."""

    pass


class SmtpdIOTimeoutException(SmtpdSignal):
    """Signal that a complete data line did not arrive within io_cmd_timeout."""

    pass


class SmtpdIOBufferOverrunException(SmtpdSignal):
    """Signal that a data line exceeded io_buffer_max_size before its LF."""

    pass


# --- SMTP dialog exceptions (carry code + text; midi 1:1) ---


class SmtpdException(Exception):
    """
    Base for SMTP dialog exceptions raised by handler logic.

    A subclass fixes ```smtp_code``` and ```smtp_text```; the constructor takes
    an optional message that is used for logging only and never leaked to the
    client. The server catches the exception and sends ```smtp_response```.

    ### Args

    - **message** (str, optional): Log-only text; not sent to the client

    ### Notes

    : ```smtp_response``` is built from the fixed code and text, not from
        ```message```, so internal details never reach the client (as in midi)

    """

    #: Default SMTP status code for this exception
    smtp_code = 554
    #: Default SMTP status text for this exception
    smtp_text = 'Transaction failed'

    def __init__(self, message=None):
        #: Optional log-only message
        self.message = message
        super().__init__(message or self.smtp_text)

    @property
    def smtp_response(self):
        """
        Render the SMTP wire response for this exception.

        ### Returns

        - **str**: The ```"<code> <text>"``` line to send to the client

        """
        return f'{self.smtp_code} {self.smtp_text}'


class Smtpd421Exception(SmtpdException):
    """421 Service too busy or not available, closing transmission channel."""

    smtp_code = 421
    smtp_text = 'Service too busy or not available, closing transmission channel'


class Smtpd432Exception(SmtpdException):
    """432 Password transition is needed."""

    smtp_code = 432
    smtp_text = 'Password transition is needed'


class Smtpd450Exception(SmtpdException):
    """450 Requested mail action not taken: mailbox unavailable."""

    smtp_code = 450
    smtp_text = 'Requested mail action not taken: mailbox unavailable'


class Smtpd451Exception(SmtpdException):
    """451 Requested action aborted: local error in processing."""

    smtp_code = 451
    smtp_text = 'Requested action aborted: local error in processing'


class Smtpd452Exception(SmtpdException):
    """452 Requested action not taken: insufficient system storage."""

    smtp_code = 452
    smtp_text = 'Requested action not taken: insufficient system storage'


class Smtpd454Exception(SmtpdException):
    """454 Temporary authentication failure."""

    smtp_code = 454
    smtp_text = 'Temporary authentication failure'


class Smtpd500Exception(SmtpdException):
    """500 Syntax error, command unrecognised or error in parameters."""

    smtp_code = 500
    smtp_text = 'Syntax error, command unrecognised or error in parameters or arguments'


class Smtpd500PipeliningException(SmtpdException):
    """500 Bad input, PIPELINING is not allowed."""

    smtp_code = 500
    smtp_text = 'Bad input, PIPELINING is not allowed'


class Smtpd500CrLfSequenceException(SmtpdException):
    """500 Bad input, Lines must be terminated by CRLF sequence."""

    smtp_code = 500
    smtp_text = 'Bad input, Lines must be terminated by CRLF sequence'


class Smtpd501Exception(SmtpdException):
    """501 Syntax error in parameters or arguments."""

    smtp_code = 501
    smtp_text = 'Syntax error in parameters or arguments'


class Smtpd502Exception(SmtpdException):
    """502 Command not implemented."""

    smtp_code = 502
    smtp_text = 'Command not implemented'


class Smtpd503Exception(SmtpdException):
    """503 Bad sequence of commands."""

    smtp_code = 503
    smtp_text = 'Bad sequence of commands'


class Smtpd504Exception(SmtpdException):
    """504 Command parameter not implemented."""

    smtp_code = 504
    smtp_text = 'Command parameter not implemented'


class Smtpd521Exception(SmtpdException):
    """521 Service does not accept mail."""

    smtp_code = 521
    smtp_text = 'Service does not accept mail'


class Smtpd530Exception(SmtpdException):
    """530 Authentication required."""

    smtp_code = 530
    smtp_text = 'Authentication required'


class Smtpd534Exception(SmtpdException):
    """534 Authentication mechanism is too weak."""

    smtp_code = 534
    smtp_text = 'Authentication mechanism is too weak'


class Smtpd535Exception(SmtpdException):
    """535 Authentication credentials invalid."""

    smtp_code = 535
    smtp_text = 'Authentication credentials invalid'


class Smtpd538Exception(SmtpdException):
    """538 Encryption required for requested authentication mechanism."""

    smtp_code = 538
    smtp_text = 'Encryption required for requested authentication mechanism'


class Smtpd550Exception(SmtpdException):
    """550 Requested action not taken: mailbox unavailable."""

    smtp_code = 550
    smtp_text = 'Requested action not taken: mailbox unavailable'


class Smtpd552Exception(SmtpdException):
    """552 Requested mail action aborted: exceeded storage allocation."""

    smtp_code = 552
    smtp_text = 'Requested mail action aborted: exceeded storage allocation'


class Smtpd553Exception(SmtpdException):
    """553 Requested action not taken: mailbox name not allowed."""

    smtp_code = 553
    smtp_text = 'Requested action not taken: mailbox name not allowed'


class Smtpd554Exception(SmtpdException):
    """554 Transaction failed."""

    smtp_code = 554
    smtp_text = 'Transaction failed'


class Tls454Exception(SmtpdException):
    """454 TLS not available."""

    smtp_code = 454
    smtp_text = 'TLS not available'


class Tls530Exception(SmtpdException):
    """530 Encryption required, must issue STARTTLS command first."""

    smtp_code = 530
    smtp_text = 'Encryption required, must issue STARTTLS command first'
