"""
Tokeo SMTPD Library.

Note: Tokeo smtpd is based on MidiSmtpServer -- a faithful translation of
midi-smtp-server (github.com/4commerce-technologies-AG/midi-smtp-server)
from Ruby to Python on ```asyncio```, keeping its dialog, events, options
and behavior. This is the single place stating that heritage.

It provides the SMTP receiving engine (```SmtpdServer```), the
per-connection ```ctx``` (```SmtpdContext```), the handler contract
(```SmtpdEvents``` with ```def``` / ```async def``` / ```@threaded``` events),
the SMTP exceptions and the sequence/mode enums.

The Tokeo extension ```tokeo.ext.smtpd``` is only the Cement glue on top of
this library (config -> services, lifecycle, CLI).

.. include:: ./SMTPD.md
"""

from .logger import Severity, ForwardingLogger
from .exc import (
    SmtpdError,
    SmtpdContractError,
    SmtpdSignal,
    SmtpdStopConnectionException,
    SmtpdStopServiceException,
    SmtpdIOTimeoutException,
    SmtpdIOBufferOverrunException,
    SmtpdException,
    Smtpd421Exception,
    Smtpd432Exception,
    Smtpd450Exception,
    Smtpd451Exception,
    Smtpd452Exception,
    Smtpd454Exception,
    Smtpd500Exception,
    Smtpd500PipeliningException,
    Smtpd500CrLfSequenceException,
    Smtpd501Exception,
    Smtpd502Exception,
    Smtpd503Exception,
    Smtpd504Exception,
    Smtpd521Exception,
    Smtpd530Exception,
    Smtpd534Exception,
    Smtpd535Exception,
    Smtpd538Exception,
    Smtpd550Exception,
    Smtpd552Exception,
    Smtpd553Exception,
    Smtpd554Exception,
    Tls454Exception,
    Tls530Exception,
)
from .context import SmtpdContext, ServerCtx, EnvelopeCtx, MessageCtx, MessageSpooler, SmtpdContextEncoder
from .events import SmtpdEvents, SmtpdEvent, threaded
from .tls import (
    TlsTransport,
    EncryptMode,
    TLS_CIPHERS_ADVANCED,
    TLS_CIPHERS_ADVANCED_PLUS,
    TLS_CIPHERS_LEGACY,
    TLS_METHODS_MODERN,
    TLS_METHODS_ADVANCED,
    TLS_METHODS_LEGACY,
)
from .server import (
    DEFAULT_SMTPD_HOST,
    DEFAULT_SMTPD_PORT,
    DEFAULT_SMTPD_PRE_FORK,
    DEFAULT_SMTPD_MAX_PROCESSINGS,
    DEFAULT_IO_WAITREADABLE_SLEEP,
    DEFAULT_IO_CMD_TIMEOUT,
    DEFAULT_IO_BUFFER_CHUNK_SIZE,
    DEFAULT_IO_BUFFER_MAX_SIZE,
    SmtpdServer,
    GlobalLimits,
    AuthMode,
)


__all__ = [
    'Severity',
    'ForwardingLogger',
    'SmtpdError',
    'SmtpdContractError',
    'SmtpdSignal',
    'SmtpdStopConnectionException',
    'SmtpdStopServiceException',
    'SmtpdIOTimeoutException',
    'SmtpdIOBufferOverrunException',
    'SmtpdException',
    'Smtpd421Exception',
    'Smtpd432Exception',
    'Smtpd450Exception',
    'Smtpd451Exception',
    'Smtpd452Exception',
    'Smtpd454Exception',
    'Smtpd500Exception',
    'Smtpd500PipeliningException',
    'Smtpd500CrLfSequenceException',
    'Smtpd501Exception',
    'Smtpd502Exception',
    'Smtpd503Exception',
    'Smtpd504Exception',
    'Smtpd521Exception',
    'Smtpd530Exception',
    'Smtpd534Exception',
    'Smtpd535Exception',
    'Smtpd538Exception',
    'Smtpd550Exception',
    'Smtpd552Exception',
    'Smtpd553Exception',
    'Smtpd554Exception',
    'Tls454Exception',
    'Tls530Exception',
    'SmtpdContext',
    'ServerCtx',
    'EnvelopeCtx',
    'MessageCtx',
    'MessageSpooler',
    'SmtpdContextEncoder',
    'SmtpdEvents',
    'SmtpdEvent',
    'threaded',
    'SmtpdServer',
    'GlobalLimits',
    'EncryptMode',
    'AuthMode',
    'DEFAULT_SMTPD_HOST',
    'DEFAULT_SMTPD_PORT',
    'DEFAULT_SMTPD_PRE_FORK',
    'DEFAULT_SMTPD_MAX_PROCESSINGS',
    'DEFAULT_IO_WAITREADABLE_SLEEP',
    'DEFAULT_IO_CMD_TIMEOUT',
    'DEFAULT_IO_BUFFER_CHUNK_SIZE',
    'DEFAULT_IO_BUFFER_MAX_SIZE',
    'TlsTransport',
    'TLS_CIPHERS_ADVANCED',
    'TLS_CIPHERS_ADVANCED_PLUS',
    'TLS_CIPHERS_LEGACY',
    'TLS_METHODS_MODERN',
    'TLS_METHODS_ADVANCED',
    'TLS_METHODS_LEGACY',
]
