"""
Tokeo SMTPD Events Contract Module.

This module defines the handler contract: a base class whose method names match
(```on_message_data_event```, ```on_rcpt_to_event```, ...). An application
implements the contract and binds it per service via config; the server
dispatches to these methods and turns any raised SMTP exception into a response.

### Features

- ```SmtpdEvent``` enum plus ```SMTPD_EVENT_NAMES``` mapping as the dispatch
    source: the server precomputes ```emit_events``` from it and call sites
    test membership with enum keys before emitting
- ```threaded``` decorator to mark a sync event method that may block, so the
    dispatcher runs it in a thread and never freezes the asyncio loop
- Two import-time guards: ```@threaded``` on an ```async def``` is rejected, and
    ```@threaded``` on ```on_message_data_receiving_event``` (the per-line hot
    path) is rejected -- both fail before any service starts
- ```self.log``` on every handler: call ```self.log.warn(...)``` (or debug/
    info/error/fatal) from any event -- never awaited, identical in ```def```,
    ```async def``` and ```@threaded def``` events; the server binds its
    logger at construction and bridges into ```on_logging_event```
- No class-wide threading default on purpose; every method decides for itself
- The full set of event methods as overridable no-ops

### Notes

: The threading model is two orthogonal axes -- placement (inline ```def``` /
    ```async def``` vs threaded ```@threaded def```) and, inline only, whether it
    may ```await```. Plain ```def``` is assumed fast; ```@threaded def``` may
    block; ```async def``` may ```await```

"""

import inspect
from enum import Enum, auto

from .exc import SmtpdContractError, Smtpd535Exception


class SmtpdEvent(Enum):
    """The dispatchable handler events (keys of ```SmtpdServer.emit_events```)."""

    ON_CONNECT_EVENT = auto()
    ON_DISCONNECT_EVENT = auto()
    ON_PROXY_EVENT = auto()
    ON_HELO_EVENT = auto()
    ON_AUTH_EVENT = auto()
    ON_MAIL_FROM_EVENT = auto()
    ON_RCPT_TO_EVENT = auto()
    ON_MESSAGE_DATA_START_EVENT = auto()
    ON_MESSAGE_DATA_RECEIVING_EVENT = auto()
    ON_MESSAGE_DATA_HEADERS_EVENT = auto()
    ON_MESSAGE_DATA_EVENT = auto()
    ON_PROCESS_LINE_UNKNOWN_EVENT = auto()
    ON_LOGGING_EVENT = auto()


#: Maps each ```SmtpdEvent``` to its handler method name on ```SmtpdEvents```
SMTPD_EVENT_NAMES = {
    SmtpdEvent.ON_CONNECT_EVENT: 'on_connect_event',
    SmtpdEvent.ON_DISCONNECT_EVENT: 'on_disconnect_event',
    SmtpdEvent.ON_PROXY_EVENT: 'on_proxy_event',
    SmtpdEvent.ON_HELO_EVENT: 'on_helo_event',
    SmtpdEvent.ON_AUTH_EVENT: 'on_auth_event',
    SmtpdEvent.ON_MAIL_FROM_EVENT: 'on_mail_from_event',
    SmtpdEvent.ON_RCPT_TO_EVENT: 'on_rcpt_to_event',
    SmtpdEvent.ON_MESSAGE_DATA_START_EVENT: 'on_message_data_start_event',
    SmtpdEvent.ON_MESSAGE_DATA_RECEIVING_EVENT: 'on_message_data_receiving_event',
    SmtpdEvent.ON_MESSAGE_DATA_HEADERS_EVENT: 'on_message_data_headers_event',
    SmtpdEvent.ON_MESSAGE_DATA_EVENT: 'on_message_data_event',
    SmtpdEvent.ON_PROCESS_LINE_UNKNOWN_EVENT: 'on_process_line_unknown_event',
    SmtpdEvent.ON_LOGGING_EVENT: 'on_logging_event',
}


def threaded(fn):
    """
    Mark a synchronous event method to run in a thread (it may block).

    A method decorated with ```@threaded``` is dispatched via a thread pool, so
    a blocking call (a DB lookup, an ```time.sleep``` teergrube) does not stall
    the asyncio event loop. Fast, non-blocking methods do not need it.

    ### Args

    - **fn** (callable): The synchronous event method to mark

    ### Returns

    - **callable**: The same method, marked with ```_smtpd_threaded = True```

    ### Raises

    - **SmtpdContractError**: If applied to an ```async def``` (a coroutine runs
        inline and can ```await``` already, so threading it is meaningless)

    ### Notes

    : This guard fires at decoration time, i.e. at import, so the mistake is
        caught before the service starts

    """
    # guard 1: @threaded on async def is meaningless (async runs inline, awaits)
    if inspect.iscoroutinefunction(fn):
        raise SmtpdContractError(
            f"@threaded on async def '{fn.__name__}' is meaningless -- "
            f'an async def runs inline and can await; drop one of the two'
        )
    fn._smtpd_threaded = True
    return fn


class SmtpdEvents:
    """
    The handler contract with the event method names.

    An application subclasses this, overrides the events it cares about, and
    binds the subclass per service via config (```type```). The ```options```
    dict from the service config is handed to the constructor. Event methods
    signal rejection by raising an ```SmtpdException``` subclass (e.g.
    ```Smtpd550Exception```); returning normally accepts.

    ### Args

    - **options** (dict, optional): The service's ```options``` block, handed
        through unevaluated by the extension for the handler to read

    ### Notes

    : ```_no_thread``` lists event methods that must stay inline. Currently only
        ```on_message_data_receiving_event```, which fires per data line -- a
        thread hop per line would be catastrophic on large messages

    : ```__init_subclass__``` enforces ```_no_thread``` at class definition time
        (import), so a forbidden ```@threaded``` fails before the service starts

    """

    #: Event methods that must never be marked ```@threaded``` (hot path)
    _no_thread = frozenset({'on_message_data_receiving_event'})

    def __init_subclass__(cls, **kwargs):
        """
        Enforce the ```_no_thread``` contract when a handler class is defined.

        ### Raises

        - **SmtpdContractError**: If a ```_no_thread``` event carries
            ```@threaded``` on the subclass or any of its bases

        """
        super().__init_subclass__(**kwargs)
        # guard 2: forbid @threaded on the hot-path events; getattr walks the
        # mro so an inherited @threaded is caught too
        for name in cls._no_thread:
            method = getattr(cls, name, None)
            if method is not None and getattr(method, '_smtpd_threaded', False):
                raise SmtpdContractError(
                    f'{cls.__name__}.{name} must not be @threaded -- '
                    f'this event fires per data line and must stay inline'
                )

    def __init__(self, options=None):
        #: The service ```options``` dict (business parameters), never evaluated
        #: by the extension
        self.options = options or {}
        #: Handler logger will be set when handler is connected to server
        self.log = None

    @staticmethod
    def authenticated(ctx):
        """
        True when the connection has authenticated.

        ### Args

        - **ctx** (SmtpdContext): The per-connection context

        ### Returns

        - **bool**: Whether ```ctx.server.authenticated``` is set

        """
        return bool(ctx.server.authenticated) and str(ctx.server.authenticated) != ''

    @staticmethod
    def encrypted(ctx):
        """
        True when the connection is TLS-encrypted.

        ### Args

        - **ctx** (SmtpdContext): The per-connection context

        ### Returns

        - **bool**: Whether ```ctx.server.encrypted``` is set

        """
        return bool(ctx.server.encrypted) and str(ctx.server.encrypted) != ''

    # --- connection lifecycle ---

    def on_connect_event(self, ctx):
        """Called on a new connection; raise ```Smtpd421Exception``` to refuse."""
        pass

    def on_disconnect_event(self, ctx):
        """Called when the connection ends."""
        pass

    def on_proxy_event(self, ctx, proxy_data):
        """
        Called when a PROXY header was parsed; raise to reject the connection.

        ### Args

        - **proxy_data** (ProxyData): The parsed PROXY v1/v2 data

        ### Notes

        : Only fires when ```proxy_protocol``` is enabled for the service; the
            real client address is then available via ```ctx.server.proxy```

        """
        pass

    # --- envelope ---

    def on_helo_event(self, ctx, helo_data):
        """Called on HELO/EHLO with the client greeting string."""
        pass

    def on_auth_event(self, ctx, authorization_id, authentication_id, authentication):
        """
        Called on AUTH; return an authorization id or raise to reject.

        ### Notes

        : If authentication is used, override this event and implement
            your own user management -- otherwise all authentications are
            blocked per default (deny with ```Smtpd535Exception```)
        : The credential check and its source (vault, DB, LDAP) live entirely in
            the handler; a returned value is used as the authorization id

        """
        self.log.debug(
            f'Deny access from {ctx.server.remote_ip}:{ctx.server.remote_port} '
            f'for {authentication_id}' + ('' if authorization_id == '' else f'/{authorization_id}') + f' with {authentication}',
        )
        raise Smtpd535Exception

    def on_mail_from_event(self, ctx, mail_from_data):
        """Called on MAIL FROM; a returned value replaces the sender if given."""
        pass

    def on_rcpt_to_event(self, ctx, rcpt_to_data):
        """Called on each RCPT TO; raise ```Smtpd550Exception``` to reject."""
        pass

    # --- message data (streamed) ---

    def on_message_data_start_event(self, ctx):
        """Called when DATA begins, before any body byte."""
        pass

    def on_message_data_receiving_event(self, ctx):
        """
        Called per data line while the body streams in (the hot path).

        ### Notes

        : Must stay inline -- ```@threaded``` here is rejected at import. Keep it
            light (e.g. an incremental size check or a teergrube via
            ```await asyncio.sleep``` in an async override)

        """
        pass

    def on_message_data_headers_event(self, ctx):
        """Called once when the headers are complete, before the body."""
        pass

    def on_message_data_event(self, ctx):
        """
        Called when the message is complete; typically enqueues raw.

        ### Notes

        : The raw body is reached via ```ctx.message.data``` / ```spool_path```
            and is never part of the ctx JSON; forwarding it is the handler's job

        """
        pass

    # --- misc ---

    def on_process_line_unknown_event(self, ctx, line):
        """Called on an unknown command line; raise to abort abusive sessions."""
        pass

    def on_logging_event(self, ctx, severity, msg, err=None):
        """Called for logging events; severity is a ```Severity``` enum member."""
        pass
