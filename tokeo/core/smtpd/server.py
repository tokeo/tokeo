"""
Tokeo SMTPD Server Module.

A one-to-one translation of MidiSmtpServer dialog engine from Ruby to
Python on ```asyncio```. ```serve_client``` owns the per-connection io loop
(timeout, buffer limit, PIPELINING guard, CRLF handling), ```process_line```
is the single command dispatcher (a case/when chain kept as if/elif with
the same regexes), and ```process_reset_session``` resets the session/ctx.

STARTTLS and AUTH (PLAIN/LOGIN) are implemented; the STARTTLS and AUTH branches
answer 500 only while ```encrypt_mode```/```auth_mode``` are FORBIDDEN.

### Notes

: Ruby's non-blocking chunk reader is replaced by asyncio's stream reader:
    ```readuntil``` with a timeout gives the same line semantics; a buffered
    next line is ```io_buffer_line_lf```.
: Command lines are handled as ```str```; the DATA body stays raw ```bytes```
    (byte-exact for DKIM), appended as ```ctx.message.data += line + line_break```.

"""

import re
import base64
import logging
import secrets
import asyncio
import inspect
import os
import sys
import socket
import ipaddress
import time
from enum import Enum, auto

from tokeo.core.utils.date import utc_now
from tokeo.core.utils.dict import deep_merge
from .context import SmtpdContext, ServerCtx, EnvelopeCtx, MessageCtx, MessageSpooler
from .tls import TlsTransport, EncryptMode
from .logger import Severity, ForwardingLogger
from .events import SmtpdEvents, SmtpdEvent, SMTPD_EVENT_NAMES


from .exc import (
    SmtpdException,
    SmtpdCloseException,
    Smtpd421CloseException,
    Smtpd554CloseException,
    SmtpdAbortException,
    SmtpdIOCmdTimeoutException,
    SmtpdIOSessionTimeoutException,
    SmtpdIOBufferOverrunException,
    Smtpd500Exception,
    Smtpd500PipeliningException,
    Smtpd500CrLfSequenceException,
    Smtpd501Exception,
    Smtpd503Exception,
    Smtpd504Exception,
    Smtpd530Exception,
    Smtpd502Exception,
    Smtpd535Exception,
    Smtpd538Exception,
    Smtpd550Exception,
    Smtpd552Exception,
    Smtpd554Exception,
    Smtpd555Exception,
    Smtpd451Exception,
    Smtpd454Exception,
    Tls454Exception,
    Tls530Exception,
)

# default constants
DEFAULT_SMTPD_HOST = '127.0.0.1'
DEFAULT_SMTPD_PORT = 2525
DEFAULT_SMTPD_MAX_PROCESSINGS = 4
DEFAULT_IO_CMD_TIMEOUT = 30
DEFAULT_IO_PROXY_CMD_TIMEOUT = 5
DEFAULT_IO_SESSION_TIMEOUT = 3600
DEFAULT_IO_BUFFER_MAX_SIZE = 1 * 1024 * 1024


class CmdSequence(Enum):
    """
    Command sequence states (the ```session[:cmd_sequence]``` symbols).

    An enum with ```auto()```, not strings: like Ruby symbols each state is one
    interned object compared by identity (```is```), so the frequent sequence
    checks are a pointer compare and a typo is a NameError at import, not a
    silent miss. The values themselves are never used.

    """

    PROXY = auto()
    HELO = auto()
    RSET = auto()
    MAIL = auto()
    RCPT = auto()
    DATA = auto()
    QUIT = auto()
    STARTTLS = auto()
    # AUTH challenge states (used when AUTH is added)
    AUTH_PLAIN_VALUES = auto()
    AUTH_LOGIN_USER = auto()
    AUTH_LOGIN_PASS = auto()


class CrlfMode(Enum):
    """The crlf_mode symbols (member names match the config values)."""

    CRLF_ENSURE = auto()
    CRLF_STRICT = auto()
    CRLF_LEAVE = auto()


class CrlfDataMode(Enum):
    """The crlf_data_mode symbols (member names match the config values)."""

    CRLF_DATA_UNSAFE = auto()
    CRLF_DATA_MODERATE = auto()
    CRLF_DATA_STRICT = auto()
    CRLF_DATA_STRICT_CLOSE = auto()


class WhitespaceMode(Enum):
    """The whitespace_mode symbols (member names match the config values)."""

    WHITESPACE_UNSAFE = auto()
    WHITESPACE_MODERATE = auto()
    WHITESPACE_STRICT = auto()


class AuthMode(Enum):
    """The auth_mode symbols (member names match the config values)."""

    AUTH_FORBIDDEN = auto()
    AUTH_OPTIONAL = auto()
    AUTH_REQUIRED = auto()


def _parse_mode(enum_cls, value, default, label):
    """Map a config string to its enum member, else a clear error."""
    name = str(value or default).upper()
    try:
        return enum_cls[name]
    except KeyError:
        allowed = ', '.join(m.name for m in enum_cls)
        raise ValueError(f'{label} must be one of {allowed}, got {value!r}')


def tls_immediate_supported():
    """
    Tell whether this Python supports the encrypt mode TLS_IMMEDIATE.

    ### Returns

    - **bool**: True on Python 3.13.13+ or 3.14.4+

    ### Notes

    : Immediate TLS needs a ```start_tls``` that keeps the ClientHello the
        server read already (gh-142352), older versions lose it

    """
    version = sys.version_info[:3]
    return version >= (3, 14, 4) or (3, 13, 13) <= version < (3, 14)


# short module-level aliases for the :CMD_* symbol names
CMD_PROXY = CmdSequence.PROXY
CMD_HELO = CmdSequence.HELO
CMD_RSET = CmdSequence.RSET
CMD_MAIL = CmdSequence.MAIL
CMD_RCPT = CmdSequence.RCPT
CMD_DATA = CmdSequence.DATA
CMD_QUIT = CmdSequence.QUIT
CMD_STARTTLS = CmdSequence.STARTTLS
CMD_AUTH_PLAIN_VALUES = CmdSequence.AUTH_PLAIN_VALUES
CMD_AUTH_LOGIN_USER = CmdSequence.AUTH_LOGIN_USER
CMD_AUTH_LOGIN_PASS = CmdSequence.AUTH_LOGIN_PASS

# reply that ends a dialog on a shutdown of the service (RFC 5321 3.8)
REPLY_421_FOR_SHUTDOWN = '421 Service shutting down and closing transmission channel'


class SmtpdSession:
    """
    Per-connection dialog state.

    ### Notes

    : ```__slots__``` keeps the field set closed: a misspelled attribute,
        read or write, raises ```AttributeError``` instead of silently
        creating a new key like a dict would, and access stays as fast as a
        string-keyed dict (writes faster)

    """

    __slots__ = ('cmd_sequence', 'ctx', 'auth_challenge', 'processing', 'data_error', 'session_deadline')

    def __init__(self):
        #: @private Current position in the SMTP command sequence
        #: (```CmdSequence```)
        self.cmd_sequence = CMD_HELO
        #: @private The per-connection ```SmtpdContext``` (built on connection
        #: start)
        self.ctx = None
        #: @private Pending AUTH LOGIN challenge state
        self.auth_challenge = {}
        #: @private True while this connection holds a processing slot
        self.processing = False
        #: @private First exception within DATA, sent as output on the end of
        #: DATA
        self.data_error = None
        #: @private Monotonic time the session must end by (io_session_timeout),
        #: set on the greeting
        self.session_deadline = None


def _parse_ports(ports):
    """
    Split a ```ports``` spec into a list of strings.

    ### Args

    - **ports**: E.g. ```'2525'```, ```'2525, 3535'```, ```'2525:3535, 2525'```

    ### Returns

    - **list**: The port specs (```':'``` ranges kept as one item)

    ### Raises

    - **ValueError**: On a missing or an empty ```''``` port item

    """
    # like Ruby split, empty items at the end are dropped ('2525,')
    value = str(ports).replace(' ', '').rstrip(',')
    items = value.split(',') if value else []
    if not items:
        raise ValueError('Missing port(s) to bind service(s) to!')
    if '' in items:
        raise ValueError('Do not use empty value "" for port(s). Please use specific port(s)!')
    return items


def _parse_hosts(hosts):
    """
    Split a ```hosts``` spec into a list.

    ### Args

    - **hosts**: E.g. ```'127.0.0.1'```, ```'127.0.0.1, ::1'```, ```'*'```
        or an already split list of host identifiers

    ### Returns

    - **list**: The host identifiers

    ### Raises

    - **ValueError**: On no hosts or an empty ```''``` inner host item

    """
    if isinstance(hosts, (list, tuple)):
        items = [str(host).strip() for host in hosts]
    else:
        # like Ruby split, empty items at the end are dropped ('127.0.0.1,')
        value = str(hosts).replace(' ', '').rstrip(',')
        items = value.split(',') if value else []
    if not items or items == ['']:
        raise ValueError('No hosts defined! Please use specific hostnames and / or ip_addresses or "*" for wildcard!')
    if '' in items:
        raise ValueError(
            'Detected an empty identifier in given hosts! Please use specific hostnames and / or ip_addresses or "*" for wildcard!'
        )
    return items


def _resolve_host(host):
    """Resolve a host to its ip addresses (an ip literal maps to itself)."""
    if host == '*':
        # all interfaces by the wildcard addresses (AI_PASSIVE), also the ones
        # added later, IPv6 only when configured (AI_ADDRCONFIG); asyncio binds
        # IPv6 with IPV6_V6ONLY, so both families bind side by side
        ips = []
        for info in socket.getaddrinfo(None, 0, proto=socket.IPPROTO_TCP, flags=socket.AI_PASSIVE | socket.AI_ADDRCONFIG):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
        return ips or ['0.0.0.0']
    try:
        # an ip literal resolves to itself (no DNS)
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    ips = []
    for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP):
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    return ips


def _build_addresses(ports, hosts):
    """
    Build the ```(ip, port)``` list from ports and hosts.

    Ports pair with hosts by index; a host past the ports list reuses the last
    ports item, and a ```':'``` port item expands to several ports.

    ### Returns

    - **list**: Tuples ```(ip, port_str)``` in bind order

    """
    addresses = []
    for index, host in enumerate(hosts):
        ip_addresses = _resolve_host(host)
        ports_for_host = (ports[index] if index < len(ports) else ports[-1]).split(':')
        for ip in ip_addresses:
            for port in ports_for_host:
                addresses.append((ip, port))
    return addresses


class GlobalLimits:
    """
    Process-wide caps shared across all services in one event loop.

    Plain counters (one loop, no threads): ```active``` open connections and
    ```active_processings``` in-progress dialogs, checked against the caps.

    ### Args

    - **max_processings** (int|None): Global concurrent-processing cap
        (None/0 = unlimited)
    - **max_connections** (int|None): Global open-connection cap
        (None/0 = unlimited)

    """

    def __init__(self, max_processings=None, max_connections=None):
        self.max_processings = int(max_processings) if max_processings else None
        self.max_connections = int(max_connections) if max_connections else None
        self.active = 0
        self.active_processings = 0
        #: @private Shared wake event: a slot released on any service wakes
        #: every waiter (each server with global_limits waits on this instead
        #: of a local one)
        self.slot_free = asyncio.Event()
        self.slot_free.set()

    def admit(self):
        """Count one more open connection."""
        self.active += 1

    def release(self):
        """Drop one open connection."""
        self.active = max(0, self.active - 1)

    def begin_processing(self):
        """Count one more in-progress dialog."""
        self.active_processings += 1

    def end_processing(self):
        """Drop one in-progress dialog."""
        self.active_processings = max(0, self.active_processings - 1)

    async def slot_wait(self):
        """
        Wait until a slot may be free again (woken via ```slot_free```).

        ### Notes

        : The base implementation waits purely on the shared event, which is
            correct whenever the counters only change inside this event loop. An
            implementation whose counters are shared ACROSS PROCESSES (where no
            event can reach this loop) should override this with a short
            re-check interval, e.g. ```wait_for(self.slot_free.wait(), 0.05)```

        """
        await self.slot_free.wait()


def _tls_cert_names(cert_cn, cert_san, hosts, addresses):
    """
    The CN and SAN for the self-signed certificate (reference parity).

    A configured ```cert_cn``` wins unchanged; without one the names derive
    from the configured hosts and addresses: hosts plus the address host
    parts, deduplicated, ```'*'``` and empties dropped; the CN becomes
    ```'localhost.local'``` when the first name is a loopback identifier,
    else the first name itself. With ```'*'``` the names and addresses of the
    local host take the place of the wildcard addresses.

    """
    if cert_cn is not None:
        return cert_cn, cert_san
    names = list(hosts) + [host for host, _port in addresses]
    # the wildcard binds all interfaces: name the local host instead (best effort)
    if '*' in hosts:
        names += ['localhost', '127.0.0.1', '::1', socket.gethostname(), socket.getfqdn()]
        try:
            names += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, proto=socket.IPPROTO_TCP)]
        except OSError:
            pass
    # neither the wildcard nor its addresses are names of the server
    sans = [san for san in dict.fromkeys(names) if san and san not in ('*', '0.0.0.0', '::')]
    if not sans or re.match(r'^(127\.0?0?0\.0?0?0\.0?0?1|::1|localhost)$', sans[0], re.IGNORECASE):
        return 'localhost.local', sans
    return sans[0], sans


class SmtpdServer:
    """
    The SMTP service: binds listeners and serves each client.

    Option names are kept 1:1; ```data_size``` is a Tokeo-only option
    (a hard cap on the message size while receiving DATA).

    ### Args

    - **events_handler** (SmtpdEvents): The bound handler implementation instance
    - **settings** (dict): The service settings
    - **global_limits** (GlobalLimits, optional): Shared caps across services
    - **logger** (optional): Target of the default ```on_logging_event```, any
        logger with ```debug```/```info```/```warning```/```error```/```critical```;
        when not given an own stderr logger with ```logger_severity``` is used

    """

    def __init__(self, events_handler, settings=None, *, global_limits=None, logger=None, debug=False, **kwargs):
        # accept both the Tokeo settings-dict and the keyword style;
        # keyword args merge into settings, which takes precedence
        settings = dict(settings or {})
        if kwargs.get('tls_mode') is not None:
            settings.setdefault('encrypt_mode', kwargs['tls_mode'])
        for key, value in kwargs.items():
            if value is not None and key not in ('ports', 'hosts', 'tls_mode', 'session_id_bytes'):
                settings.setdefault(key, value)
        self.events_handler = events_handler
        self.emit_events = self._build_emit_events_dispatcher(events_handler)
        self.debug = bool(debug)
        self.global_limits = global_limits
        #: Strong refs to scheduled log tasks (the loop only holds them weakly)
        self._pending_log_tasks = set()
        #: The serving loop, captured at start() for thread-safe log scheduling
        self._asyncio_running_loop = None
        # target of the default on_logging_event: the given logger, else an own
        # stderr logger; set on mounting, a value set on the handler is replaced
        logger_severity = settings.get('logger_severity')
        if logger_severity is not None and logger_severity not in Severity.__members__:
            raise ValueError(f'Severity of the server logger (logger_severity) must be one of {", ".join(Severity.__members__)}!')
        if logger is not None and logger_severity is not None:
            raise ValueError('Severity of the server logger (logger_severity) must not be set with an own logger!')
        if isinstance(logger, ForwardingLogger):
            raise ValueError('The server logger (logger) must not be the log input of a handler!')
        if logger is None:
            logger = logging.Logger('smtpd')
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s: [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
            logger.addHandler(handler)
            logger.setLevel(Severity[logger_severity or 'WARNING'].value)
        events_handler._logger_protected = logger
        #: The exposed logger; forwards to the handler's on_logging_event
        self.logger = ForwardingLogger(self._log_bridge)
        events_handler.log = self.logger
        # ports / hosts / addresses
        # bytes of randomness for the per-connection session id (ctx.id);
        # a keyword-only server property, never read from service settings
        self.session_id_bytes = int(kwargs.get('session_id_bytes') or 8)
        self.ports = _parse_ports(kwargs.get('ports') if kwargs.get('ports') is not None else settings.get('ports', DEFAULT_SMTPD_PORT))
        self.hosts = _parse_hosts(kwargs.get('hosts') if kwargs.get('hosts') is not None else settings.get('hosts', DEFAULT_SMTPD_HOST))
        self._addresses = _build_addresses(self.ports, self.hosts)
        # dialog options
        self.io_cmd_timeout = settings.get('io_cmd_timeout', DEFAULT_IO_CMD_TIMEOUT)
        # like Ruby nil, None is the default
        self.io_session_timeout = settings.get('io_session_timeout')
        if self.io_session_timeout is None:
            self.io_session_timeout = DEFAULT_IO_SESSION_TIMEOUT
        self.io_buffer_max_size = settings.get('io_buffer_max_size')
        if self.io_buffer_max_size is None:
            self.io_buffer_max_size = DEFAULT_IO_BUFFER_MAX_SIZE
        self.crlf_mode = _parse_mode(CrlfMode, settings.get('crlf_mode'), 'CRLF_ENSURE', 'crlf_mode')
        self.crlf_data_mode = _parse_mode(
            CrlfDataMode,
            settings.get('crlf_data_mode'),
            'CRLF_DATA_STRICT' if self.crlf_mode is CrlfMode.CRLF_STRICT else 'CRLF_DATA_MODERATE',
            'crlf_data_mode',
        )
        self.whitespace_mode = _parse_mode(WhitespaceMode, settings.get('whitespace_mode'), 'WHITESPACE_MODERATE', 'whitespace_mode')
        self.pipelining_extension = bool(settings.get('pipelining_extension', False))
        self.proxy_extension = bool(settings.get('proxy_extension', False))
        self.io_proxy_cmd_timeout = settings.get('io_proxy_cmd_timeout', DEFAULT_IO_PROXY_CMD_TIMEOUT)
        # test for message spooling
        # value has to be an existing directory + file-name prefix
        value = settings.get('spool')
        if value:
            value = str(value)
            self._spool_dir, self._spool_prefix = os.path.split(value)
            if not os.path.isdir(self._spool_dir):
                raise ValueError(f'spool: directory does not exist: {self._spool_dir!r}')
            self._spooling = True
        else:
            self._spooling = False
        self.internationalization_extensions = bool(settings.get('internationalization_extensions', False))
        self.do_dns_reverse_lookup = settings.get('do_dns_reverse_lookup')
        if self.do_dns_reverse_lookup is None:
            self.do_dns_reverse_lookup = True
        self.encrypt_mode = _parse_mode(EncryptMode, settings.get('encrypt_mode'), 'TLS_FORBIDDEN', 'encrypt_mode')
        self.auth_mode = _parse_mode(AuthMode, settings.get('auth_mode'), 'AUTH_FORBIDDEN', 'auth_mode')
        self.trusted_relay_auth_ids = settings.get('trusted_relay_auth_ids', [])
        self.max_processings = settings.get('max_processings', DEFAULT_SMTPD_MAX_PROCESSINGS)
        self.max_connections = settings.get('max_connections', None)
        # validation
        if self.crlf_mode is CrlfMode.CRLF_STRICT and self.crlf_data_mode not in (
            CrlfDataMode.CRLF_DATA_STRICT,
            CrlfDataMode.CRLF_DATA_STRICT_CLOSE,
        ):
            raise ValueError('Line breaks in DATA (crlf_data_mode) must be CRLF_DATA_STRICT or CRLF_DATA_STRICT_CLOSE with CRLF_STRICT!')
        if self.encrypt_mode is EncryptMode.TLS_IMMEDIATE and not tls_immediate_supported():
            raise ValueError('Immediate TLS (encrypt_mode TLS_IMMEDIATE) requires Python 3.13.13+ or 3.14.4+!')
        if not (type(self.io_cmd_timeout) in (int, float) and self.io_cmd_timeout >= 1):
            raise ValueError('Timeout for a command line (io_cmd_timeout) must be at least 1 second!')
        if not (type(self.io_proxy_cmd_timeout) in (int, float) and self.io_proxy_cmd_timeout >= 1):
            raise ValueError('Timeout for the PROXY header (io_proxy_cmd_timeout) must be at least 1 second!')
        if not (type(self.io_session_timeout) in (int, float) and 1 <= self.io_session_timeout <= 86400):
            raise ValueError('Timeout of a session (io_session_timeout) must be between 1 and 86400 seconds!')
        if not (type(self.io_buffer_max_size) is int and self.io_buffer_max_size > 0):
            raise ValueError('Maximum size of a line (io_buffer_max_size) must be a positive integer!')
        if not (
            isinstance(self.trusted_relay_auth_ids, list)
            and all(isinstance(i, str) and i != '' and i == i.strip() for i in self.trusted_relay_auth_ids)
        ):
            raise ValueError('Trusted relays (trusted_relay_auth_ids) must be a list of stripped non-empty authorization ids!')
        if not (isinstance(self.max_processings, int) and self.max_processings > 0):
            raise ValueError('Number of simultaneous processings (max_processings) must be a positive integer!')
        if self.max_connections is not None:
            if not (isinstance(self.max_connections, int) and self.max_connections > 0):
                raise ValueError('Number of concurrent connections (max_connections) must be nil or a positive integer!')
            if self.max_connections < self.max_processings:
                raise ValueError(
                    'Number of concurrent connections (max_connections) is lower than '
                    'number of simultaneous processings (max_processings)!'
                )
        # Tokeo-only option: hard cap on the message size during DATA
        self.data_size = settings.get('data_size', None)
        #: TLS transport, built when encryption is allowed (else None and
        #: STARTTLS answers 500)
        self.tls = None
        if self.encrypt_mode is not EncryptMode.TLS_FORBIDDEN:
            # reference parity: without a configured common name the CN and
            # SAN of a self-signed certificate derive from hosts + addresses
            tls_cert_cn, tls_cert_san = _tls_cert_names(
                settings.get('tls_cert_cn'), settings.get('tls_cert_san'), self.hosts, self._addresses
            )
            self.tls = TlsTransport(
                cert_path=settings.get('tls_cert_path'),
                key_path=settings.get('tls_key_path'),
                cert=settings.get('tls_cert'),
                key=settings.get('tls_key'),
                ciphers=settings.get('tls_ciphers'),
                methods=settings.get('tls_methods'),
                cert_cn=tls_cert_cn,
                cert_san=tls_cert_san,
                logger=lambda severity, msg: self._log_bridge(None, severity, msg),
            )
        self._servers = []
        # connection/processing bookkeeping (one event loop: plain counters
        # + an asyncio.Event as the condition-variable equivalent)
        self._connections = 0
        self._processings = 0
        # with global limits the wake event is the SHARED one, so a slot
        # released on any service wakes waiters on every service
        self._slot_free = global_limits.slot_free if global_limits is not None else asyncio.Event()
        self._slot_free.set()
        # graceful shutdown state
        self._shutdown = False
        self._stopped = True
        self._sessions = set()
        self._asyncio_shutdown_signal = None
        self._serve_task = None

    @property
    def addresses(self):
        """The bind addresses as ```'ip:port'``` strings."""
        return [f'{ip}:{port}' for ip, port in self._addresses]

    def _default_listeners(self):
        """Listener dicts derived from the configured ports/hosts."""
        return [{'host': ip, 'port': int(port)} for ip, port in self._addresses]

    def _log_bridge(self, ctx, severity, msg):
        """
        Sync entry point for logging: bridge into the async event dispatch.

        Called from synchronous code (```server.logger``` and the handler's
        bound ```self.log```, the TLS transport). It never executes the
        handler event itself; it hands the ```_emit``` coroutine to the
        event loop, so the execution model (sync / async / ```@threaded```)
        stays entirely with the precomputed dispatcher.

        ### Notes

        - Called on the loop thread: scheduled as a fire-and-forget task
        - Called from another thread (e.g. inside a ```@threaded``` event):
            scheduled thread-safe onto the server loop
        - No loop running (e.g. ```server.logger``` before ```start()```):
            the coroutine is run to completion in place

        """
        if SmtpdEvent.ON_LOGGING_EVENT not in self.emit_events:
            return
        coro = self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, severity, msg, None)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if self._asyncio_running_loop is not None and self._asyncio_running_loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, self._asyncio_running_loop)
            else:
                asyncio.run(coro)
            return
        task = loop.create_task(coro)
        # keep a strong reference; the loop only holds tasks weakly
        self._pending_log_tasks.add(task)
        task.add_done_callback(self._pending_log_tasks.discard)

    # ---- listeners ----------------------------------------------------------

    async def _bind(self, listeners):
        """Bind every listener and record it."""
        self._shutdown = False
        self._stopped = False
        self._asyncio_running_loop = asyncio.get_running_loop()
        self._asyncio_shutdown_signal = self._asyncio_running_loop.create_future()
        if listeners is None:
            listeners = self._default_listeners()
        if self.crlf_data_mode is CrlfDataMode.CRLF_DATA_UNSAFE:
            self.logger.warning('Line breaks in DATA (crlf_data_mode) CRLF_DATA_UNSAFE allow SMTP smuggling!')
        if self.whitespace_mode is WhitespaceMode.WHITESPACE_UNSAFE:
            self.logger.warning('Whitespace of commands (whitespace_mode) WHITESPACE_UNSAFE passes control chars to the events!')
        for lst in listeners:
            host, port = lst.get('host', '127.0.0.1'), int(lst.get('port', 2525))
            self.logger.info(f'Starting service on {f"[{host}]" if ":" in host else host}:{port or "(auto)"}')
            server = await asyncio.start_server(
                self._on_connection,
                host=host,
                port=port,
                limit=self.io_buffer_max_size,
                reuse_port=bool(lst.get('reuse_port', False)),
            )
            # a name may bind several addresses, port 0 gets its port only now
            for sock in server.sockets:
                ip, bound = sock.getsockname()[:2]
                self.logger.info(f'Service listening on {f"[{ip}]" if ":" in ip else ip}:{bound}')
            self._servers.append(server)

    async def _serve_forever(self):
        """Serve all bound listeners until cancelled."""
        try:
            # the listeners serve since start_server; wait here and not in the
            # serve_forever of asyncio, which on Python 3.13 closes every client
            # connection as soon as stop() closes the listeners
            await asyncio.get_running_loop().create_future()
        except asyncio.CancelledError:
            # cancelled by stop(); the listeners are already closed
            pass
        finally:
            self._stopped = True

    async def serve(self, listeners=None):
        """
        Bind every ```(host, port)``` and serve until stopped (blocking).

        ### Args

        - **listeners** (list): Dicts with ```host```/```port``` per listener

        """
        await self._bind(listeners)
        self._serve_task = asyncio.current_task()
        await self._serve_forever()

    async def start(self, listeners=None):
        """
        Bind and serve in the background, returning once bound.

        Unlike ```serve``` (which blocks), this returns after the listeners are
        bound, so the caller can later stop/join it programmatically.

        ### Args

        - **listeners** (list): Dicts with ```host```/```port``` per listener

        """
        await self._bind(listeners)
        self._serve_task = asyncio.ensure_future(self._serve_forever())
        # yield control so serve_forever is running before returning
        await asyncio.sleep(0)

    def shutdown(self):
        """Schedule a graceful shutdown."""
        self._shutdown = True
        # signal to the dialogs that wait for input, from any thread
        if self._asyncio_running_loop is not None and not self._asyncio_running_loop.is_closed():
            self._asyncio_running_loop.call_soon_threadsafe(self._shutdown_signal)

    def _shutdown_signal(self):
        """Send the shutdown signal once."""
        if self._asyncio_shutdown_signal is not None and not self._asyncio_shutdown_signal.done():
            self._asyncio_shutdown_signal.set_result(b'')

    def shutdown_requested(self):
        """True once a shutdown was scheduled."""
        return self._shutdown

    def stopped(self):
        """True once serving has fully stopped."""
        return self._stopped and not self._sessions

    @property
    def connections(self):
        """Number of connected clients."""
        return self._connections

    def has_connections(self):
        """True while any client is connected."""
        return self._connections > 0

    @property
    def processings(self):
        """Number of clients processing a message."""
        return self._processings

    def has_processings(self):
        """True while a message is being processed."""
        return self._processings > 0

    async def stop(self, wait_seconds_before_close=2.0, gracefully=True):
        """
        Stop the server, optionally draining active connections.

        ### Args

        - **wait_seconds_before_close** (float): How long to let active
            connections finish before forcing them down
        - **gracefully** (bool): When True, let each active dialog end after
            its current command; when False, drop connections immediately

        ### Notes

        : Stop accepting first, then either drain within the time budget
            or force the remaining sessions down by cancelling their tasks,
            each after a last attempt to send 421

        """
        # always stop accepting new connections first
        self.close()
        # signal every active dialog to end
        self.shutdown()
        if gracefully:
            # wait for active sessions to finish, up to the time budget
            loop = asyncio.get_running_loop()
            deadline = loop.time() + max(0.0, wait_seconds_before_close)
            while self._sessions and loop.time() < deadline:
                await asyncio.sleep(0.05)
        # force any remaining sessions down
        for task in list(self._sessions):
            task.cancel()
        # stop the background serve task if start() was used
        if self._serve_task is not None:
            self._serve_task.cancel()
        # wait briefly for cancelled sessions to unwind their finally blocks
        for _ in range(50):
            if not self._sessions:
                break
            await asyncio.sleep(0.01)
        self._stopped = True

    async def join(self, poll_seconds=0.1):
        """Block until the server has fully stopped."""
        while not self.stopped():
            await asyncio.sleep(poll_seconds)

    def close(self):
        """Stop serving on all listeners."""
        for s in self._servers:
            s.close()

    async def _on_connection(self, reader, writer):
        """
        Accept one client and serve it.

        Every accepted connection is counted and served; the
        max_connections / max_processings policy is enforced inside
        ```serve_client``` *after* ```on_connect_event```, so the handler sees
        every connection on connect and may decide what to do.

        """
        # add to list of connections
        self._connections += 1
        if self.global_limits is not None:
            self.global_limits.admit()
        # track the running task so a graceful stop can drain or cancel it
        task = asyncio.current_task()
        self._sessions.add(task)
        session = SmtpdSession()
        try:
            await self.serve_client(session, reader, writer)
        except asyncio.CancelledError:
            # service shutdown / forced drop: end this connection
            pass
        # a fault outside the dialog, e.g. of on_disconnect_event
        except Exception as e:  # noqa: B902
            await self._emit(SmtpdEvent.ON_LOGGING_EVENT, None, Severity.CRITICAL, f'{e} ({type(e).__name__})', e)
        finally:
            self._sessions.discard(task)
            # drop from connections and processings; wake the next waiter
            self._connections = max(0, self._connections - 1)
            if self.global_limits is not None:
                self.global_limits.release()
            if session.processing:
                self._processings = max(0, self._processings - 1)
                if self.global_limits is not None:
                    self.global_limits.end_processing()
            self._slot_free.set()

    def _too_many_connections(self):
        """True when connections exceed max_connections (service or global cap)."""
        if self.max_connections is not None and self._connections > int(self.max_connections):
            return True
        gl = self.global_limits
        if gl is not None and gl.max_connections is not None and gl.active > gl.max_connections:
            return True
        return False

    async def _acquire_processing_slot(self, session):
        """
        Block until a processing slot is free, then take it.

        Ruby: ```@connections_cv.wait until processings < max_processings``` then
        ```@processings << Thread.current```. With one event loop this is an
        ```asyncio.Event``` re-checked in a loop.

        """
        limit = int(self.max_processings) if self.max_processings is not None else None
        gl = self.global_limits
        glimit = gl.max_processings if gl is not None else None

        def _slot_ok():
            svc_ok = limit is None or self._processings < limit
            glob_ok = glimit is None or gl.active_processings < glimit
            return svc_ok and glob_ok

        while True:
            if _slot_ok():
                self._processings += 1
                if gl is not None:
                    gl.begin_processing()
                session.processing = True
                return
            # arm the wake-up, then re-check once: a release between the failed
            # check above and clear() would otherwise be swallowed (lost wakeup)
            self._slot_free.clear()
            if _slot_ok():
                continue
            if gl is not None:
                # the wait strategy belongs to the limits implementation: the
                # in-process base waits on the event; a cross-process variant
                # (shared counters under pre_fork) overrides slot_wait
                await gl.slot_wait()
            else:
                await self._slot_free.wait()

    def _auth_available(self, ctx):
        """
        True when AUTH may be offered and accepted on this channel right now.

        ### Notes

        : On an unencrypted channel ```TLS_WHEN_AUTH``` and ```TLS_REQUIRED```
            refuse AUTH command (530) and EHLO does not advertise it. After
            STARTTLS or without TLS enforcement, the advertising just follows
            ```auth_mode``` setting.

        """
        # False if we don't want to support AUTH
        if self.auth_mode is AuthMode.AUTH_FORBIDDEN:
            return False
        # True, if wanting AUTH and the channel is already encrypted
        if ctx.server.encrypted:
            return True
        # False, if channel is not encrypted but AUTH needs it or TLS_REQUIRED
        if self.encrypt_mode in (EncryptMode.TLS_WHEN_AUTH, EncryptMode.TLS_REQUIRED):
            return False
        # True, if TLS is FORBIDDEN or OPTIONAL
        if self.encrypt_mode in (EncryptMode.TLS_FORBIDDEN, EncryptMode.TLS_OPTIONAL):
            return True
        # False, in any other not matched case yet
        return False

    def _greet_message(self, session):
        """
        To be done
        """
        # return greet response when in CMD_HELO sequence
        # like Ruby to_s, None is an empty text
        return f'220 {str(session.ctx.server.local_response or "").strip()}' if session.cmd_sequence is CMD_HELO else ''

    # ---- per-connection dialog --------------------------

    async def serve_client(self, session, reader, writer):
        """
        Handle one connection (the ported serve_client loop).

        ### Args

        - **session** (SmtpdSession): The dialog state (cmd_sequence,
            auth_challenge, ctx)
        - **reader** (StreamReader): The connection input stream
        - **writer** (StreamWriter): The connection output stream

        """
        try:
            try:
                # ON CONNECTION
                # Reset and initialize message
                self.process_reset_session(session, connection_initialize=True)
                ctx = session.ctx

                sockname = writer.get_extra_info('sockname') or ('', None)
                ctx.server.local_ip, ctx.server.local_port = sockname[0], sockname[1]
                peername = writer.get_extra_info('peername') or ('', None)
                ctx.server.remote_ip, ctx.server.remote_port = peername[0], peername[1]
                # resolve hostnames (numeric unless do_dns_reverse_lookup)
                ctx.server.local_host = ctx.server.local_ip
                ctx.server.remote_host = ctx.server.remote_ip
                if self.do_dns_reverse_lookup:
                    ctx.server.local_host = await self._reverse_lookup(ctx.server.local_ip) or ctx.server.local_ip
                    ctx.server.remote_host = await self._reverse_lookup(ctx.server.remote_ip) or ctx.server.remote_ip

                ctx.server.connected = utc_now()

                local_name = (
                    (f'[IPv6:{ctx.server.local_ip}]' if ':' in ctx.server.local_ip else f'[{ctx.server.local_ip}]')
                    if ctx.server.local_ip == ctx.server.local_host
                    else ctx.server.local_host
                )
                ctx.server.local_response = f'{local_name} says welcome!'
                ctx.server.helo_response = f'{local_name} at your service!'

                # check if we want to let this remote station connect us
                if SmtpdEvent.ON_CONNECT_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_CONNECT_EVENT, ctx)

                # drop connection (respond 421) if too busy
                if self._too_many_connections():
                    raise Smtpd421CloseException('Abort connection while too busy, exceeding max_connections!')

                # when processings exceed the maximum allowed, wait for a free slot
                await self._acquire_processing_slot(session)

                # a graceful shutdown refuses a connection that was not greeted yet
                if self._shutdown:
                    raise Smtpd421CloseException('Abort connection while service is shutting down!')

                # io_session_timeout limits the time a connection holds its slot
                session.session_deadline = time.monotonic() + self.io_session_timeout

                # initialize \r\n line_break for CRLF_ENSURE and CRLF_STRICT
                line_break = b'\r\n'
                # the line before ended by CRLF, needed by the end of DATA
                line_break_before_has_crlf = True

                # greet on connect only when CMD_HELO and empty ctx.server.helo
                output = self._greet_message(session)

                while True:
                    if output:
                        if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                            await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.DEBUG, f'>>> {output}')

                        await self._write_line(writer, output.encode('utf-8', 'surrogateescape') + b'\r\n')

                    if session.cmd_sequence is CMD_QUIT:
                        break

                    # a graceful shutdown ends the dialog
                    if self._shutdown:
                        break

                    if session.cmd_sequence is CMD_STARTTLS:
                        # STARTTLS discards plaintext buffered before the handshake
                        # so commands injected right after STARTTLS cannot run over
                        # the encrypted channel (CVE-2011-0411 class); immediate TLS
                        # keeps it, start_tls hands the buffered ClientHello over
                        buffered = getattr(reader, '_buffer', None)
                        if buffered and self.encrypt_mode is not EncryptMode.TLS_IMMEDIATE:
                            del buffered[:]
                        await writer.start_tls(self.tls.context, ssl_handshake_timeout=self.io_cmd_timeout)
                        ctx.server.encrypted = utc_now()
                        # client must re-HELO; HELO, AUTH and the transaction are
                        # dropped (RFC 3207 4.2)
                        session.cmd_sequence = CMD_HELO
                        ctx.reset_on_starttls()
                        # immediate TLS greets only now, on the encrypted channel
                        if self.encrypt_mode is EncryptMode.TLS_IMMEDIATE:
                            # send the server greeting next
                            output = self._greet_message(session)
                            # proceed with loop
                            continue

                    raw = await self._read_line(
                        reader,
                        self.io_proxy_cmd_timeout if session.cmd_sequence is CMD_PROXY else self.io_cmd_timeout,
                        session.session_deadline,
                    )

                    # an empty raw comes from a close of the client (also within
                    # a line) or from a shutdown that ended the wait in _wait_line
                    if raw == b'':
                        # a shutdown ends by 421, a closed peer gets no reply
                        if self._shutdown:
                            break
                        # not by a shutdown: the client has closed the input
                        raise EOFError

                    try:
                        # pipelining extension or violation
                        if not (
                            (session.cmd_sequence is CMD_DATA)
                            or self.pipelining_extension
                            or (session.cmd_sequence is CMD_PROXY)
                            or not self._buffered_line_lf(reader)
                        ):
                            raise Smtpd500PipeliningException

                        # test that <CRLF>.<CRLF> ends DATA (RFC 5321 4.1.1.4),
                        # CRLF_DATA_UNSAFE ignores it (SMTP smuggling)
                        # the last line ended by CRLF and this raw is b'.\r\n'
                        data_dialog_end_rfc_conform = line_break_before_has_crlf and raw == b'.\r\n'
                        # renew the last line flag by this line (raw) or sequence
                        line_break_before_has_crlf = raw.endswith(b'\r\n') or session.cmd_sequence is not CMD_DATA
                        # in DATA the strict modes allow no other CR in the raw;
                        # raw comes from _read_line, so its last char is always LF
                        if (
                            # inside the data dialog
                            session.cmd_sequence is CMD_DATA
                            # strict modes are enforced
                            and self.crlf_data_mode in (CrlfDataMode.CRLF_DATA_STRICT, CrlfDataMode.CRLF_DATA_STRICT_CLOSE)
                            # not ending by CRLF, or another CR before the CRLF
                            and (not raw.endswith(b'\r\n') or raw.find(b'\r', 0, -2) >= 0)
                        ):
                            # a bare CR or LF makes the message invalid
                            raise Smtpd554Exception if self.crlf_data_mode is CrlfDataMode.CRLF_DATA_STRICT else Smtpd554CloseException

                        if self.crlf_mode is CrlfMode.CRLF_ENSURE:
                            line = raw.replace(b'\r', b'').replace(b'\n', b'')
                        elif self.crlf_mode is CrlfMode.CRLF_LEAVE:
                            # use the input line break, a line always ends by LF
                            line_break = b'\r\n' if raw.endswith(b'\r\n') else b'\n'
                            if session.cmd_sequence is CMD_DATA:
                                ctx.message.crlf = line_break
                            line = raw.removesuffix(line_break)
                        elif self.crlf_mode is CrlfMode.CRLF_STRICT:
                            if not raw.endswith(b'\r\n'):
                                raise Smtpd500CrLfSequenceException
                            line = raw.removesuffix(b'\r\n')
                            if line.find(b'\r') >= 0:
                                raise Smtpd500Exception('Line contains additional CR chars!')
                        else:
                            raise Smtpd500Exception(f'Unknown crlf_mode {self.crlf_mode}!')

                        # a dot line that does not end DATA stays data, passed on
                        # dot-stuffed to keep its dot
                        if (
                            line == b'.'
                            and not data_dialog_end_rfc_conform
                            and session.cmd_sequence is CMD_DATA
                            and self.crlf_data_mode is not CrlfDataMode.CRLF_DATA_UNSAFE
                        ):
                            line = b'..'

                        # log the received command line (never the DATA payload);
                        # the DATA check first: it skips the membership test and
                        # the decoding for every body line
                        if session.cmd_sequence is not CMD_DATA and SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                            await self._emit(
                                SmtpdEvent.ON_LOGGING_EVENT,
                                ctx,
                                Severity.DEBUG,
                                '<<< ' + line.decode('utf-8', 'backslashreplace').replace('\r', '[\\r]'),
                            )

                        output = await self.process_line(session, line, line_break)

                    # an SmtpdException answers by its code, any other error by
                    # 500; within DATA only after its end, then any other by 451
                    except Exception as e:  # noqa: B902
                        # exit the loop at once, also within DATA
                        if isinstance(e, SmtpdAbortException):
                            raise
                        # within DATA keep the first error, the end of DATA raises
                        # it again: read DATA to its end, answer then (RFC 1870);
                        # a SmtpdCloseException too closes only after the end
                        if session.cmd_sequence is CMD_DATA:
                            if session.data_error is None:
                                session.data_error = e
                                if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                                    await self._emit(
                                        SmtpdEvent.ON_LOGGING_EVENT,
                                        ctx,
                                        Severity.ERROR,
                                        f'Error within DATA, further DATA is ignored up to its end: {e} ({type(e).__name__})',
                                        e,
                                    )
                            output = ''
                            # go on reading DATA until end, nothing to answer
                            continue
                        # exit the loop, the outer handler answers and closes
                        if isinstance(e, SmtpdCloseException):
                            raise
                        ctx.server.exceptions += 1
                        ctx.server.errors.append(e)
                        if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                            await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.ERROR, f'{e} ({type(e).__name__})', e)
                        # respond the smtp error of the exception to the client
                        output = e.smtp_response if isinstance(e, SmtpdException) else Smtpd500Exception().smtp_response

                    # still in CMD_PROXY: the header failed, end without a dialog
                    if session.cmd_sequence is CMD_PROXY:
                        raise Smtpd421CloseException(f'Abort connection while PROXY failed! ({output})')

                # QUIT closes with 221, a shutdown with 421 (RFC 5321 3.8)
                output = '221 Service closing transmission channel' if session.cmd_sequence is CMD_QUIT else REPLY_421_FOR_SHUTDOWN
                await self._write_line(writer, output.encode() + b'\r\n')

            # the peer closed the connection, also within a line
            except EOFError as e:
                if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                    await self._emit(
                        SmtpdEvent.ON_LOGGING_EVENT, session.ctx, Severity.DEBUG, 'Connection lost due abort by client! (EOFError)', e
                    )

            # the peer reset the connection: count and log it like Ruby, a 421
            # can not arrive any more
            except ConnectionError as e:
                ctx = session.ctx
                ctx.server.exceptions += 1
                ctx.server.errors.append(e)
                if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.ERROR, 'Connection lost due reset by client!', e)

            except asyncio.CancelledError:
                # a stop drops the connection, after a last attempt to send 421
                # (RFC 5321 3.8)
                if self._shutdown:
                    try:
                        # no drain: a client that does not read must not block
                        writer.write(REPLY_421_FOR_SHUTDOWN.encode() + b'\r\n')
                    except (OSError, RuntimeError):
                        pass
                raise

            # any other error drops the connection, a SmtpdCloseException by
            # its code, else by 421
            except Exception as e:  # noqa: B902
                ctx = session.ctx
                ctx.server.exceptions += 1
                ctx.server.errors.append(e)
                if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.ERROR, f'{e} ({type(e).__name__})', e)
                _err = e if isinstance(e, SmtpdCloseException) else Smtpd421CloseException()
                try:
                    # a reply stuck in the send buffer: the client does not
                    # read, a further one would not arrive either
                    if writer.transport.get_write_buffer_size():
                        raise ConnectionAbortedError('Client does not read its replies!')
                    await self._write_line(writer, _err.smtp_response.encode() + b'\r\n')
                except (OSError, SmtpdAbortException) as e2:
                    if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                        await self._emit(
                            SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.DEBUG, f"Can't send {_err.smtp_code} abort code! (IOError)", e2
                        )

        finally:
            try:
                # event for cleanup at end of communication
                if SmtpdEvent.ON_DISCONNECT_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_DISCONNECT_EVENT, session.ctx)
            finally:
                # also when on_disconnect_event failed
                try:
                    # close() keeps the socket open until the send buffer is
                    # flushed, abort() drops a client that does not read
                    if writer.transport.get_write_buffer_size():
                        writer.transport.abort()
                    else:
                        writer.close()
                except OSError:
                    pass
                # finalize the session at least here, drop maybe interrupted parts
                self.process_reset_session(session)

    async def _read_line(self, reader, io_timeout, session_deadline):
        """
        Read one raw line (with terminator) enforcing timeouts and buffer size.

        ### Args

        - **reader** (StreamReader): The connection input stream
        - **io_timeout** (int|float): Seconds to wait for a complete line
        - **session_deadline** (float): Monotonic time the session must end by

        ### Returns

        - **bytes**: The line, always ending by LF; b'' when the peer closed,
            also within a line, or a shutdown ended the wait

        ### Raises

        - **SmtpdIOCmdTimeoutException**: No line within io timeout (421)
        - **SmtpdIOSessionTimeoutException**: Session above io_session_timeout,
            also checked for a buffered line (421)
        - **SmtpdIOBufferOverrunException**: Buffer above io_buffer_max_size
            (554); all bubble to the outer handler which closes the connection

        """
        # also a client that streams endless lines (NOOP or DATA) ends here
        session_remaining = session_deadline - time.monotonic()
        if session_remaining <= 0:
            raise SmtpdIOSessionTimeoutException('Abort connection while exceeding io_session_timeout!')
        try:
            # the timeout and the shutdown guard *waiting on the wire*: when a
            # complete line is already buffered, readuntil returns without
            # waiting; in pipelined/DATA bulk traffic that is nearly every line
            if self._buffered_line_lf(reader):
                raw = await reader.readuntil(b'\n')
            else:
                raw = await self._wait_line(reader, min(io_timeout, session_remaining))
        except asyncio.TimeoutError:
            if session_remaining < io_timeout:
                raise SmtpdIOSessionTimeoutException('Abort connection while exceeding io_session_timeout!')
            raise SmtpdIOCmdTimeoutException('Abort connection while no complete line within io timeout!')
        except asyncio.LimitOverrunError:
            raise SmtpdIOBufferOverrunException('Abort connection while line exceeds io_buffer_max_size!')
        except asyncio.IncompleteReadError:
            # the peer closed, a rest without LF is dropped
            return b''
        if len(raw) > self.io_buffer_max_size:
            raise SmtpdIOBufferOverrunException('Abort connection while line exceeds io_buffer_max_size!')
        return raw

    async def _write_line(self, writer, data):
        """
        Write one reply and wait for the client to take it within io_cmd_timeout.

        ### Args

        - **writer** (StreamWriter): The connection output stream
        - **data** (bytes): The reply with its line break

        ### Raises

        - **SmtpdIOCmdTimeoutException**: Reply not taken within
            io_cmd_timeout (421); io_session_timeout does not cut a reply

        """
        writer.write(data)
        # with an empty send buffer drain does not wait, the timeout costs
        # about 8 us per reply: only take it for a client that reads slowly
        # or not at all
        if not writer.transport.get_write_buffer_size():
            await writer.drain()
            return
        try:
            async with asyncio.timeout(self.io_cmd_timeout):
                await writer.drain()
        except TimeoutError:
            raise SmtpdIOCmdTimeoutException('Abort connection while the client does not take its replies!')

    async def _wait_line(self, reader, io_timeout):
        """Wait for the next line, b'' once a shutdown ends the wait."""
        if self._shutdown:
            return b''
        read = asyncio.ensure_future(reader.readuntil(b'\n'))
        # a server that was never started has no shutdown signal yet
        waits = {read} if self._asyncio_shutdown_signal is None else {read, self._asyncio_shutdown_signal}
        try:
            done, _ = await asyncio.wait(waits, timeout=io_timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # no read outlives the wait, also not on a forced cancel
            if not read.done():
                read.cancel()
        if read in done:
            # a line that arrived together with the shutdown is still served
            return read.result()
        if self._shutdown:
            return b''
        raise asyncio.TimeoutError

    @staticmethod
    def _buffered_line_lf(reader):
        """io_buffer_line_lf: is a further complete line already buffered?"""
        return getattr(reader, '_buffer', b'').find(b'\n') >= 0

    async def _reverse_lookup(self, ip):
        """Reverse-resolve an IP to a hostname (best effort)."""
        if not ip:
            return None
        try:
            loop = asyncio.get_running_loop()
            name = await loop.run_in_executor(None, socket.gethostbyaddr, ip)
            return name[0]
        except (OSError, socket.herror):
            return None

    # ---- the command dispatcher --------------------------

    #: Flags of every command regex: the verbs match case-insensitive in ASCII
    #: only, so "ſ" is no "s" and a no-break space no "\s" (RFC 5321 2.4); a
    #: constant, as "re.IGNORECASE | re.ASCII" costs about 0.5 us per use
    _RE_CMD_FLAGS = re.IGNORECASE | re.ASCII

    #: A control char except HTAB (NUL, CR, LF, VT, FF, ESC, DEL ...);
    #: WHITESPACE_MODERATE answers one inside a command line by 501
    _RE_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0a-\x1f\x7f]')

    #: Any whitespace except SP and HTAB (e.g. a no-break space, the Unicode
    #: spaces, VT, FF) or a control char except HTAB; WHITESPACE_STRICT
    #: answers one after the verb by 501
    _RE_UNUSUAL_CHARS = re.compile(r'[^\S \t]|[\x00-\x08\x0a-\x1f\x7f]')

    #: Any whitespace (Unicode) and control chars, also NUL, at the start or
    #: the end of a line; WHITESPACE_MODERATE removes them as noise
    _RE_NOISE_AT_EDGES = re.compile(r'^[\s\x00-\x1f\x7f]+|[\s\x00-\x1f\x7f]+$')

    #: The verb (ASCII letters) of a command, any whitespace (Unicode) as
    #: separator and the rest; WHITESPACE_MODERATE separates by it
    _RE_VERB_ANY_SEPARATOR = re.compile(r'([A-Za-z]+)\s+(.*)$')

    #: The verb (ASCII letters) of a command, SP or HTAB as separator and the
    #: rest; WHITESPACE_STRICT and WHITESPACE_UNSAFE separate by it
    _RE_VERB_SP_HTAB_SEPARATOR = re.compile(r'([A-Za-z]+)[ \t]+(.*)$')

    async def process_line(self, session, line, line_break):
        """
        Process one dialog line (the ported process_line dispatcher).

        ### Args

        - **session** (dict): The session hash (cmd_sequence,
            auth_challenge, ctx)
        - **line** (bytes): The line after crlf_mode handling (no terminator)
        - **line_break** (bytes): The line break to append in DATA mode

        ### Returns

        - **str**: The SMTP response, or '' for silence (DATA lines, QUIT,
            PROXY before immediate TLS)

        """
        ctx = session.ctx

        if session.cmd_sequence is not CMD_DATA:
            # commands are text; decode byte-exact
            line = line.decode('utf-8', 'surrogateescape')
            # whitespace_mode, not for the PROXY header: MODERATE strips unusual
            # whitespace and control chars at the edges as noise and refuses
            # control chars inside, STRICT and UNSAFE strip only SP and HTAB
            if session.cmd_sequence is not CMD_PROXY:
                if self.whitespace_mode is WhitespaceMode.WHITESPACE_MODERATE:
                    line = self._RE_NOISE_AT_EDGES.sub('', line)
                else:
                    line = line.strip(' \t')
                # one SP separates the verb (ASCII) of a command from its
                # argument; MODERATE separates by any whitespace, STRICT and
                # UNSAFE by SP and HTAB, without one the command is unknown
                if session.cmd_sequence not in (CMD_AUTH_PLAIN_VALUES, CMD_AUTH_LOGIN_USER, CMD_AUTH_LOGIN_PASS):
                    if self.whitespace_mode is WhitespaceMode.WHITESPACE_MODERATE:
                        match = self._RE_VERB_ANY_SEPARATOR.match(line)
                    else:
                        match = self._RE_VERB_SP_HTAB_SEPARATOR.match(line)
                    if match:
                        # STRICT refuses unusual whitespace and control chars
                        if self.whitespace_mode is WhitespaceMode.WHITESPACE_STRICT and self._RE_UNUSUAL_CHARS.search(match.group(2)):
                            raise Smtpd501Exception
                        line = f'{match.group(1)} {match.group(2)}'
                # MODERATE refuses control chars inside, a separator is gone now
                if self.whitespace_mode is WhitespaceMode.WHITESPACE_MODERATE and self._RE_CONTROL_CHARS.search(line):
                    raise Smtpd501Exception

        # the client cancels a running AUTH exchange (RFC 4954 section 4)
        if line == '*' and session.cmd_sequence in (CMD_AUTH_PLAIN_VALUES, CMD_AUTH_LOGIN_USER, CMD_AUTH_LOGIN_PASS):
            session.cmd_sequence = CMD_RSET
            session.auth_challenge = {}
            raise Smtpd501Exception

        if session.cmd_sequence is CMD_AUTH_PLAIN_VALUES:
            # handle authentication
            return await self.process_auth_plain(session, line)

        elif session.cmd_sequence is CMD_AUTH_LOGIN_USER:
            # handle authentication
            return self.process_auth_login_user(session, line)

        elif session.cmd_sequence is CMD_AUTH_LOGIN_PASS:
            # handle authentication
            return await self.process_auth_login_pass(session, line)

        elif session.cmd_sequence is not CMD_DATA:
            # PROXY
            # Docs: haproxy/doc/proxy-protocol.txt (github.com/haproxy)
            # syntax
            # PROXY PROTO source-ip dest-ip source-port dest-port
            if session.cmd_sequence is CMD_PROXY:
                # with proxy_extension the PROXY header has to be the first line
                if not re.match(r'^PROXY(\s+)', line, self._RE_CMD_FLAGS):
                    raise Smtpd421CloseException('Abort connection while PROXY is not the first command!')
                # test valid proxy command params: at most 107 bytes with CRLF,
                # exactly one SP between the fields, ports without leading zeros
                # (haproxy doc/proxy-protocol.txt, version 1)
                if len(line) > 105 or not re.match(
                    r'^PROXY (UNKNOWN( .*)?|TCP[46] [0-9a-f.:]+ [0-9a-f.:]+ (0|[1-9][0-9]{0,4}) (0|[1-9][0-9]{0,4}))$',
                    line,
                    self._RE_CMD_FLAGS,
                ):
                    raise Smtpd421CloseException('Abort connection while illegal PROXY command!')
                # extract params
                cmd_data = re.sub(r'^PROXY\ ', '', line, flags=self._RE_CMD_FLAGS).strip().split()
                proxy_data = {
                    'proto': cmd_data[0].upper(),
                    'source_ip': None,
                    'source_host': None,
                    'source_port': None,
                    'dest_ip': None,
                    'dest_host': None,
                    'dest_port': None,
                }
                # test proto
                if proxy_data['proto'] != 'UNKNOWN':
                    try:
                        # try to build valid addresses from given strings
                        proxy_data['source_ip'] = ipaddress.ip_address(cmd_data[1])
                        proxy_data['source_port'] = int(cmd_data[3])
                        proxy_data['dest_ip'] = ipaddress.ip_address(cmd_data[2])
                        proxy_data['dest_port'] = int(cmd_data[4])
                        expected = 4 if proxy_data['proto'] == 'TCP4' else 6
                        if proxy_data['source_ip'].version != expected or proxy_data['dest_ip'].version != expected:
                            raise ValueError
                        if not (0 <= proxy_data['source_port'] <= 65535 and 0 <= proxy_data['dest_port'] <= 65535):
                            raise ValueError
                        # normalize ip addresses
                        proxy_data['source_ip'] = str(proxy_data['source_ip'])
                        proxy_data['source_host'] = proxy_data['source_ip']
                        proxy_data['dest_ip'] = str(proxy_data['dest_ip'])
                        proxy_data['dest_host'] = proxy_data['dest_ip']
                    except (ValueError, IndexError):
                        # change exception into Smtpd exception and drop connection
                        raise Smtpd421CloseException('Abort connection for unsupported PROXY parameters!')
                if SmtpdEvent.ON_PROXY_EVENT in self.emit_events:
                    return_value = await self._emit(SmtpdEvent.ON_PROXY_EVENT, ctx, proxy_data)
                    # None keeps the parsed data, a dict updates its fields (also
                    # {}), else a fault of the handler
                    if isinstance(return_value, dict):
                        deep_merge(proxy_data, return_value)
                    elif return_value is not None:
                        raise Smtpd421CloseException(f'on_proxy_event must not return {return_value!r}')
                ctx.server.proxy = proxy_data
                # the server greeting must follow the PROXY header, not the connect
                if self.encrypt_mode is EncryptMode.TLS_IMMEDIATE:
                    # on TLS_IMMEDIATE the connection must be encrypted next
                    session.cmd_sequence = CMD_STARTTLS
                    # do not send the server greeting yet
                    return ''
                else:
                    # continue with the HELO sequence
                    session.cmd_sequence = CMD_HELO
                    # send the server greeting next
                    return self._greet_message(session)

            elif self.proxy_extension and re.match(r'^PROXY(\ .*)?$', line, self._RE_CMD_FLAGS):
                # PROXY is only valid as first line, a later one is an abuse
                raise Smtpd421CloseException('Abort connection while PROXY already set!')

            # Handle specific messages from the client
            elif re.match(r'^(HELO|EHLO)(\ .*)?$', line, self._RE_CMD_FLAGS):
                # a later HELO/EHLO resets the transaction like RSET
                # an AUTH stays (RFC 5321 4.1.4)
                if session.cmd_sequence is not CMD_HELO:
                    self.process_reset_session(session)
                cmd_data = re.sub(r'^(HELO|EHLO)\ ?', '', line, flags=self._RE_CMD_FLAGS)
                if SmtpdEvent.ON_HELO_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_HELO_EVENT, ctx, cmd_data)
                elif cmd_data == '':
                    # without a handler an empty domain is a syntax error
                    raise Smtpd501Exception
                ctx.server.helo = cmd_data
                session.cmd_sequence = CMD_RSET
                # like Ruby to_s, None is an empty text
                helo_response = str(ctx.server.helo_response or '').strip()
                if re.match(r'^EHLO', line, self._RE_CMD_FLAGS):
                    # the greeting line, then the extensions of this channel
                    lines = [helo_response]
                    if self.internationalization_extensions:
                        lines += ['8BITMIME', 'SMTPUTF8']
                    if self.pipelining_extension:
                        lines.append('PIPELINING')
                    if self._auth_available(ctx):
                        lines.append('AUTH LOGIN PLAIN')
                    if self.encrypt_mode is not EncryptMode.TLS_FORBIDDEN and not ctx.server.encrypted:
                        lines.append('STARTTLS')
                    # the last line ends the reply without a dash (RFC 5321 4.1.1.1)
                    return '\r\n'.join([f'250-{text}' for text in lines[:-1]] + [f'250 {lines[-1]}'])
                else:
                    return f'250 {helo_response}'.strip()

            elif re.match(r'^STARTTLS(\ .*)?$', line, self._RE_CMD_FLAGS):
                # STARTTLS
                if self.encrypt_mode is EncryptMode.TLS_FORBIDDEN:
                    raise Smtpd500Exception
                # STARTTLS takes no argument
                if ' ' in line:
                    raise Smtpd501Exception
                if session.cmd_sequence is CMD_HELO:
                    raise Smtpd503Exception
                if not self.tls:
                    raise Tls454Exception
                if ctx.server.encrypted:
                    raise Smtpd503Exception
                session.cmd_sequence = CMD_STARTTLS
                # return with new service ready message
                return '220 Ready to start TLS'

            elif re.match(r'^AUTH(\ .*)?$', line, self._RE_CMD_FLAGS):
                # AUTH
                if self.auth_mode is AuthMode.AUTH_FORBIDDEN:
                    raise Smtpd500Exception
                if session.cmd_sequence is not CMD_RSET:
                    raise Smtpd503Exception
                if not self._auth_available(ctx):
                    # TLS_WHEN_AUTH asks for encryption by AUTH (RFC 4954 6),
                    # TLS_REQUIRED by every command (RFC 3207 4)
                    raise Smtpd538Exception if self.encrypt_mode is EncryptMode.TLS_WHEN_AUTH else Tls530Exception
                if ctx.server.authenticated:
                    raise Smtpd503Exception
                # the mechanism and an optional initial response, split by
                # whitespace_mode
                auth_data = re.sub(r'^AUTH\ ?', '', line, flags=self._RE_CMD_FLAGS)
                if self.whitespace_mode is WhitespaceMode.WHITESPACE_MODERATE:
                    auth_data = auth_data.split()
                else:
                    auth_data = [value for value in re.split(r'[ \t]+', auth_data) if value]
                if len(auth_data) not in (1, 2):
                    raise Smtpd501Exception
                # handle auth command
                if auth_data[0].upper() == 'PLAIN':
                    if len(auth_data) == 1:
                        session.cmd_sequence = CMD_AUTH_PLAIN_VALUES
                        # response code include post ending with a space
                        return '334 '
                    else:
                        # handle authentication with given auth_id and password
                        return await self.process_auth_plain(session, auth_data[1])

                elif auth_data[0].upper() == 'LOGIN':
                    if len(auth_data) == 1:
                        # reset auth_challenge
                        session.auth_challenge = {}
                        session.cmd_sequence = CMD_AUTH_LOGIN_USER
                        # response code with request for Username
                        return '334 ' + base64.b64encode(b'Username:').decode()

                    else:
                        # handle next sequence
                        return self.process_auth_login_user(session, auth_data[1])

                # CRAM-MD5 is not supported in case of also unencrypted data
                # delivery; instead of supporting password encryption only, an
                # optional SMTPS service should be provided

                else:
                    # unsupported auth mechanism (RFC 4954 section 4)
                    raise Smtpd504Exception

            elif re.match(r'^NOOP(\ .*)?$', line, self._RE_CMD_FLAGS):
                # NOOP, an argument is ignored (RFC 5321 4.1.1.9)
                return '250 OK'

            elif re.match(r'^RSET(\ .*)?$', line, self._RE_CMD_FLAGS):
                # RSET takes no argument
                if ' ' in line:
                # RSET is valid at any time (RFC 5321 4.1.1.5, 4.1.4)
                    raise Smtpd501Exception
                if self.encrypt_mode is EncryptMode.TLS_REQUIRED and not ctx.server.encrypted:
                    raise Tls530Exception
                self.process_reset_session(session)
                return '250 OK'
                # continue with CMD_HELO when in it
                was_cmd_helo = session.cmd_sequence is CMD_HELO
                # always clean session on RSET

                # need to restore CMD_HELO state
                if was_cmd_helo:
                    session.cmd_sequence = CMD_HELO
            elif re.match(r'^QUIT(\ .*)?$', line, self._RE_CMD_FLAGS):
                # QUIT takes no argument
                if ' ' in line:
                    raise Smtpd501Exception
                session.cmd_sequence = CMD_QUIT
                return ''

            elif re.match(r'^MAIL FROM:', line, self._RE_CMD_FLAGS):
                # MAIL
                if session.cmd_sequence is not CMD_RSET:
                    raise Smtpd503Exception
                if self.encrypt_mode is EncryptMode.TLS_REQUIRED and not ctx.server.encrypted:
                    raise Tls530Exception
                if self.auth_mode is AuthMode.AUTH_REQUIRED and not ctx.server.authenticated:
                    raise Smtpd530Exception
                # a new transaction, nothing of a rejected MAIL remains
                ctx.reset_on_mail_from()
                cmd_data = re.sub(r'^MAIL FROM:', '', line, flags=self._RE_CMD_FLAGS)
                # separate the address as sent from its params (RFC 5321 4.1.2),
                # split by whitespace_mode
                if self.whitespace_mode is WhitespaceMode.WHITESPACE_MODERATE:
                    cmd_data, params = re.match(r'\s*(<[^>]*>|\S*)\s*(.*)$', cmd_data).groups()
                    params = params.split()
                else:
                    cmd_data, params = re.match(r'[ \t]*(<[^>]*>|[^ \t]*)[ \t]*(.*)$', cmd_data).groups()
                    params = [param for param in re.split(r'[ \t]+', params) if param]
                keywords = []
                for param in params:
                    match = re.match(r'([A-Za-z0-9][A-Za-z0-9-]*)(?:=([\x21-\x3c\x3e-\x7e]+))?$', param)
                    # a malformed or a repeated parameter
                    if not match or match.group(1).upper() in keywords:
                        raise Smtpd501Exception
                    keyword, value = match.group(1).upper(), match.group(2)
                    keywords.append(keyword)
                    # only the parameters announced by EHLO here are valid
                    if keyword == 'BODY' and self.internationalization_extensions:
                        # 8BITMIME (RFC 1652)
                        if value is None or value.upper() not in ('7BIT', '8BITMIME'):
                            raise Smtpd501Exception
                        ctx.envelope.encoding_body = value.lower()
                    elif keyword == 'SMTPUTF8' and self.internationalization_extensions:
                        # SMTPUTF8 (RFC 6531)
                        if value is not None:
                            raise Smtpd501Exception
                        ctx.envelope.encoding_utf8 = 'utf8'
                    elif keyword == 'AUTH' and self._auth_available(ctx):
                        # AUTH= of the original submitter (RFC 4954 section 5)
                        if value is None or not re.match(r'([\x21-\x2a\x2c-\x3c\x3e-\x7e]|\+[0-9A-Fa-f]{2})+$', value):
                            raise Smtpd501Exception
                        # decode the xtext ("+" with two hex digits is one char)
                        auth_for_id_raw = re.sub(r'\+([0-9A-Fa-f]{2})', lambda m: chr(int(m.group(1), 16)), value)
                        ctx.envelope.auth_for_id_raw = auth_for_id_raw
                        # only a trusted relay may name the submitter
                        if (
                            auth_for_id_raw.strip() not in ('', '<>')
                            and ctx.server.authorization_id.strip() != ''
                            and ctx.server.authorization_id in self.trusted_relay_auth_ids
                        ):
                            ctx.envelope.auth_for_id = auth_for_id_raw.strip()
                    else:
                        # unknown or not announced here (RFC 5321 4.1.1.11)
                        raise Smtpd555Exception
                if SmtpdEvent.ON_MAIL_FROM_EVENT in self.emit_events:
                    try:
                        return_value = await self._emit(SmtpdEvent.ON_MAIL_FROM_EVENT, ctx, cmd_data)
                    except SmtpdException:
                        raise
                    # any other handler fault is a local error
                    except Exception as e:  # noqa: B902
                        raise Smtpd451Exception(f'{e} ({type(e).__name__})')
                    # None/False refuses, True keeps, a stripped string replaces
                    if return_value is None or return_value is False:
                        raise Smtpd550Exception
                    if isinstance(return_value, str) and return_value == return_value.strip():
                        cmd_data = return_value
                    elif return_value is not True:
                        raise Smtpd451Exception(f'on_mail_from_event must not return {return_value!r}')
                ctx.envelope.mail_from = cmd_data
                session.cmd_sequence = CMD_MAIL
                return '250 OK'

            elif re.match(r'^RCPT TO:', line, self._RE_CMD_FLAGS):
                # RCPT
                if session.cmd_sequence not in (CMD_MAIL, CMD_RCPT):
                    raise Smtpd503Exception
                if self.encrypt_mode is EncryptMode.TLS_REQUIRED and not ctx.server.encrypted:
                    raise Tls530Exception
                if self.auth_mode is AuthMode.AUTH_REQUIRED and not ctx.server.authenticated:
                    raise Smtpd530Exception
                cmd_data = re.sub(r'^RCPT TO:', '', line, flags=self._RE_CMD_FLAGS)
                # separate the address as sent, RCPT TO announces no params, split
                # by whitespace_mode
                if self.whitespace_mode is WhitespaceMode.WHITESPACE_MODERATE:
                    cmd_data, params = re.match(r'\s*(<[^>]*>|\S*)\s*(.*)$', cmd_data).groups()
                else:
                    cmd_data, params = re.match(r'[ \t]*(<[^>]*>|[^ \t]*)[ \t]*(.*)$', cmd_data).groups()
                if params:
                    raise Smtpd555Exception
                if SmtpdEvent.ON_RCPT_TO_EVENT in self.emit_events:
                    try:
                        return_value = await self._emit(SmtpdEvent.ON_RCPT_TO_EVENT, ctx, cmd_data)
                    except SmtpdException:
                        raise
                    # any other handler fault is a local error
                    except Exception as e:  # noqa: B902
                        raise Smtpd451Exception(f'{e} ({type(e).__name__})')
                    # None/False refuses, True keeps, a stripped string replaces
                    if return_value is None or return_value is False:
                        raise Smtpd550Exception
                    if isinstance(return_value, str) and return_value == return_value.strip():
                        cmd_data = return_value
                    elif return_value is not True:
                        raise Smtpd451Exception(f'on_rcpt_to_event must not return {return_value!r}')
                ctx.envelope.rcpt_tos.append(cmd_data)
                session.cmd_sequence = CMD_RCPT
                return '250 OK'

            elif re.match(r'^DATA(\ .*)?$', line, self._RE_CMD_FLAGS):
                # DATA takes no argument
                if ' ' in line:
                    raise Smtpd501Exception
                if session.cmd_sequence is not CMD_RCPT:
                    raise Smtpd503Exception
                if self.encrypt_mode is EncryptMode.TLS_REQUIRED and not ctx.server.encrypted:
                    raise Tls530Exception
                if self.auth_mode is AuthMode.AUTH_REQUIRED and not ctx.server.authenticated:
                    raise Smtpd530Exception
            elif re.match(r'^(VRFY|EXPN|HELP)(\ .*)?$', line, self._RE_CMD_FLAGS):
                # VRFY, EXPN and HELP are valid at any time (RFC 5321 4.1.4)
                if self.encrypt_mode is EncryptMode.TLS_REQUIRED and not ctx.server.encrypted:
                    raise Tls530Exception
                verb = line[:4].upper()
                cmd_data = re.sub(r'^(VRFY|EXPN|HELP)\ ?', '', line, flags=self._RE_CMD_FLAGS)
                event = {'VRFY': SmtpdEvent.ON_VRFY_EVENT, 'EXPN': SmtpdEvent.ON_EXPN_EVENT}.get(verb, SmtpdEvent.ON_HELP_EVENT)
                reply = await self._emit_reply(event, ctx, cmd_data)
                if reply:
                    return reply
                # without an answer of the handler VRFY neither verifies nor
                # denies (RFC 5321 7.3), EXPN and HELP are not implemented
                if verb == 'VRFY':
                    return '252 Cannot VRFY user, but will accept message and attempt delivery'
                raise Smtpd502Exception

                session.cmd_sequence = CMD_DATA
                ctx.message.received = utc_now()
                return '354 Enter message, ending with "." on a line by itself'

            else:
                # If we somehow get to this point then
                # allow handling of unknown command line
                reply = await self._emit_reply(SmtpdEvent.ON_PROCESS_LINE_UNKNOWN_EVENT, ctx, line)
                if reply:
                    return reply
                # an unknown command: the serve loop counts it, records the error,
                # logs it and answers 500
                raise Smtpd500Exception

        else:
            # If we are in data mode then ...

            # the first line of DATA, not after an error
            if session.data_error is None and ctx.message.bytesize < 0:
                if SmtpdEvent.ON_MESSAGE_DATA_START_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_MESSAGE_DATA_START_EVENT, ctx)
                # always initialize bytesize on data
                ctx.message.bytesize = len(ctx.message.data)

            # ... and the entire new message data (line) does NOT consist
            # solely of a period (.) on a line by itself then we are being
            # told to continue data mode
            if line != b'.':
                # on error drop anything until end of data dialog
                if session.data_error:
                    return ''

                # remove a preceding first dot (RFC 5321 section-4.5.2)
                if line[:1] == b'.':
                    line = line[1:]

                # if received an empty line the first time, that identifies
                # end of headers
                if not ctx.message.headers and not line:
                    # change flag to do not signal this again
                    ctx.message.headers = True
                    if SmtpdEvent.ON_MESSAGE_DATA_HEADERS_EVENT in self.emit_events:
                        await self._emit(SmtpdEvent.ON_MESSAGE_DATA_HEADERS_EVENT, ctx)
                        # repair bytesize, maybe changed by headers
                        ctx.message.bytesize = len(ctx.message.data)

                    # create the spool file and handle if activated
                    if self._spooling:
                        # timestamp from the connection start
                        stamp = ctx.server.connected.strftime('%Y%m%d-%H%M%S')
                        # create the spooler for message spooling
                        ctx.message.spooler = MessageSpooler(dir=self._spool_dir, prefix=f'{self._spool_prefix}{stamp}-', debug=self.debug)
                        # write current data completely into spool
                        ctx.message.spooler.file.write(ctx.message.data)

                # take next chunk and make sure to add CR LF as defined by RFC
                chunk = line + line_break

                if ctx.message.headers and ctx.message.spooler:
                    # write into spool
                    ctx.message.spooler.file.write(chunk)
                    # save last line ending for final chomp
                    ctx.message.spooler.last_line_break = line_break
                else:
                    # in-memory
                    ctx.message.data += chunk

                # update bytesize
                ctx.message.bytesize += len(chunk)

                # Tokeo-only option: enforce a hard data_size cap
                if self.data_size and ctx.message.bytesize > int(self.data_size):
                    raise Smtpd552Exception

                # e.g. abort while receiving too big incoming mail or
                # create a teergrube for spammers etc.
                if SmtpdEvent.ON_MESSAGE_DATA_RECEIVING_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_MESSAGE_DATA_RECEIVING_EVENT, ctx)

                # just return and stay on CMD_DATA
                return ''

            # otherwise the entire new message data (line) consists
            # solely of a period on a line by itself then we are being
            # told to finish data mode

            # remove last CR LF pair or single LF
            # and update bytesize of message data
            ctx.message.chomp()

            # save delivered UTC time
            ctx.message.delivered = utc_now()

            # end of data dialog processing
            try:
                # when recorded an error while receiving data, raise it now
                if session.data_error:
                    raise session.data_error

                # call event to process message
                if SmtpdEvent.ON_MESSAGE_DATA_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_MESSAGE_DATA_EVENT, ctx)

                # signal client successfully processed message
                return '250 Requested mail action okay, completed'

            # test for SmtpdException
            except SmtpdException:
                # just re-raise exception set by app
                raise

            # test all other Exceptions
            except Exception as e:  # noqa: B902 - Ruby: StandardError => 451
                # send correct aborted message to smtp dialog
                raise Smtpd451Exception(str(e)) from e

            finally:
    async def _emit_reply(self, event, ctx, data):
        """
        Emit an event whose handler may answer by a single reply line.

        ### Args

        - **event** (SmtpdEvent): The event to emit
        - **ctx** (SmtpdContext): The context of the connection
        - **data** (str): The argument of the command, or the whole line

        ### Returns

        - **str|None**: The reply line with its code, or None when the handler
            is not overridden or leaves the answer to the server (None/False)

        ### Raises

        - **Smtpd451Exception**: On any other result, or on an exception other
            than an SmtpdException

        """
        if event not in self.emit_events:
            return None
        try:
            return_value = await self._emit(event, ctx, data)
        except SmtpdException:
            raise
        # any other handler fault is a local error
        except Exception as e:  # noqa: B902
            raise Smtpd451Exception(f'{e} ({type(e).__name__})')
        if isinstance(return_value, str) and re.fullmatch(r'[2-5][0-9]{2}( [^\r\n]*)?', return_value):
            return return_value
        if return_value is not None and return_value is not False:
            raise Smtpd451Exception(f'{SMTPD_EVENT_NAMES[event]} must not return {return_value!r}')
        return None

                # always start with empty values after finishing incoming message
                # and rset command sequence
                self.process_reset_session(session)

    # ---- authentication --------------------------------

    @staticmethod
    def _decode64(encoded):
        """
        Decode a base64 response of the AUTH dialog (RFC 4954 section 4).

        Only the base64 alphabet is valid, surrounding spaces and a missing
        padding are tolerated.

        ### Args

        - **encoded** (str): The base64 text from the dialog

        ### Returns

        - **str**: The decoded value (byte-exact via surrogateescape)

        ### Raises

        - **Smtpd501Exception**: The text is not valid base64

        """
        encoded = (encoded or '').strip(' \t')
        # a single "=" is an empty initial response (RFC 4954 section 4)
        if encoded == '=':
            return ''
        if not re.fullmatch(r'[A-Za-z0-9+/]*={0,2}', encoded):
            raise Smtpd501Exception
        try:
            raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), validate=True)
        except ValueError:
            raise Smtpd501Exception
        return raw.decode('utf-8', 'surrogateescape')

    async def process_auth_plain(self, session, encoded_auth_response):
        """
        Handle plain authentication.

        ### Args

        - **session** (dict): The session hash
        - **encoded_auth_response** (str): Base64 ```authzid\\0authcid\\0password```

        ### Returns

        - **str**: '235 OK' on success (rejects raise SMTP exceptions)

        """
        try:
            # extract auth id (and password)
            auth_values = self._decode64(encoded_auth_response).split('\x00')
            # authzid NUL authcid NUL passwd, authcid and passwd set (RFC 4616)
            if len(auth_values) != 3 or not auth_values[1] or not auth_values[2]:
                raise Smtpd535Exception
            # call event function to test credentials
            try:
                return_value = await self._emit(SmtpdEvent.ON_AUTH_EVENT, session.ctx, auth_values[0], auth_values[1], auth_values[2])
            except SmtpdException:
                raise
            # any other handler fault is a temporary failure (RFC 4954 section 6)
            except Exception as e:  # noqa: B902
                raise Smtpd454Exception(f'{e} ({type(e).__name__})')
            # only a returned authorization id grants the access
            if not (isinstance(return_value, str) and return_value):
                raise Smtpd535Exception
            if return_value != return_value.strip():
                raise Smtpd454Exception(f'on_auth_event must return a stripped authorization id, not {return_value!r}')
            # save authentication information to ctx
            ctx = session.ctx
            ctx.server.authorization_id = return_value
            ctx.server.authentication_id = auth_values[1]
            ctx.server.authenticated = utc_now()
            # response code
            return '235 OK'

        finally:
            # whatever happens in this check, reset next sequence
            session.cmd_sequence = CMD_RSET

    def process_auth_login_user(self, session, encoded_auth_response):
        """
        Handle the LOGIN username step.

        ### Args

        - **session** (dict): The session hash
        - **encoded_auth_response** (str): Base64 username

        ### Returns

        - **str**: The 334 password challenge

        """
        try:
            # save challenged auth_id
            session.auth_challenge['authorization_id'] = ''
            session.auth_challenge['authentication_id'] = self._decode64(encoded_auth_response)
        except SmtpdException:
            # a fault of the decoding ends the AUTH exchange
            session.cmd_sequence = CMD_RSET
            session.auth_challenge = {}
            raise
        session.cmd_sequence = CMD_AUTH_LOGIN_PASS
        # response code with request for Password
        return '334 ' + base64.b64encode(b'Password:').decode()

    async def process_auth_login_pass(self, session, encoded_auth_response):
        """
        Handle the LOGIN password step.

        ### Args

        - **session** (dict): The session hash
        - **encoded_auth_response** (str): Base64 password

        ### Returns

        - **str**: '235 OK' on success (rejects raise SMTP exceptions)

        """
        try:
            # extract auth id (and password)
            auth_values = [
                session.auth_challenge['authorization_id'],
                session.auth_challenge['authentication_id'],
                self._decode64(encoded_auth_response),
            ]
            # authentication id and password must be set
            if not auth_values[1] or not auth_values[2]:
                raise Smtpd535Exception
            # check for valid credentials
            try:
                return_value = await self._emit(SmtpdEvent.ON_AUTH_EVENT, session.ctx, auth_values[0], auth_values[1], auth_values[2])
            except SmtpdException:
                raise
            # any other handler fault is a temporary failure (RFC 4954 section 6)
            except Exception as e:  # noqa: B902
                raise Smtpd454Exception(f'{e} ({type(e).__name__})')
            # only a returned authorization id grants the access
            if not (isinstance(return_value, str) and return_value):
                raise Smtpd535Exception
            if return_value != return_value.strip():
                raise Smtpd454Exception(f'on_auth_event must return a stripped authorization id, not {return_value!r}')
            # save authentication information to ctx
            ctx = session.ctx
            ctx.server.authorization_id = return_value
            ctx.server.authentication_id = auth_values[1]
            ctx.server.authenticated = utc_now()
            # response code
            return '235 OK'

        finally:
            # whatever happens in this check, reset next sequence
            session.cmd_sequence = CMD_RSET
            # and reset auth_challenge
            session.auth_challenge = {}

    # ---- session reset --------------------------

    def process_reset_session(self, session, connection_initialize=False):
        """
        Reset the context of the current smtpd dialog.

        ### Args

        - **session** (SmtpdSession): The session state to reset
        - **connection_initialize** (bool): True on connection start; builds the
            ctx and sets the sequence to CMD_PROXY, CMD_STARTTLS (implicit TLS)
            or CMD_HELO, else CMD_RSET

        """
        # set active command sequence info
        if not connection_initialize:
            session.cmd_sequence = CMD_RSET
        elif self.proxy_extension:
            session.cmd_sequence = CMD_PROXY
        elif self.encrypt_mode is EncryptMode.TLS_IMMEDIATE:
            session.cmd_sequence = CMD_STARTTLS
        else:
            session.cmd_sequence = CMD_HELO
        # drop any auth challenge
        session.auth_challenge = {}
        # reset error signaling for data dialog
        session.data_error = None
        # test existing of ctx
        if session.ctx is None:
            # create a new one
            session.ctx = SmtpdContext(id=secrets.token_hex(self.session_id_bytes))
        else:
            # make sure to finish all interrupted actions
            session.ctx.finalize()
        ctx = session.ctx
        # reset server values (only on connection start)
        if connection_initialize:
            ctx.server = ServerCtx()
        # reset envelope and message values
        ctx.envelope = EnvelopeCtx()
        ctx.message = MessageCtx()

    # ---- event dispatch -------------------------------------------------------

    #: Dispatch kinds, resolved once per handler class (see the builder below)
    _EMIT_SYNC, _EMIT_ASYNC, _EMIT_THREAD = 0, 1, 2

    def _build_emit_events_dispatcher(self, events_handler):
        """
        Precompute the dispatch table ```SmtpdEvent -> (kind, bound method)```.

        ### Args

        - **events_handler** (SmtpdEvents): The bound handler instance

        ### Returns

        - **dict**: ```{event_name: (kind, method)}``` for dispatchable events

        ### Notes

        : Resolves per event *once* what ```_emit``` previously resolved on
            every call (```getattr``` + ```iscoroutinefunction``` + threaded
            marker), cutting the per-event dispatch cost to a dict lookup

        : Events the handler did not override keep NO entry when their base is
            a no-op, so call sites skip ```_emit``` (and its coroutine) entirely
            via ```in self.emit_events```; ```ON_AUTH_EVENT``` always keeps
            an entry because its base carries behaviour (the default deny
            raises 535 when not overridden)

        """
        dispatcher = {}
        for ev in SmtpdEvent:
            name = SMTPD_EVENT_NAMES[ev]
            method = getattr(events_handler, name, None)
            if method is None:
                continue
            overridden = getattr(type(events_handler), name, None) is not getattr(SmtpdEvents, name, None)
            # the defaults of these events carry behaviour and run without override
            if not overridden and ev not in (
                SmtpdEvent.ON_AUTH_EVENT,
                SmtpdEvent.ON_LOGGING_EVENT,
                SmtpdEvent.ON_CONNECT_EVENT,
                SmtpdEvent.ON_DISCONNECT_EVENT,
            ):
                continue
            if inspect.iscoroutinefunction(method):
                kind = self._EMIT_ASYNC
            elif getattr(method, '_smtpd_threaded', False):
                kind = self._EMIT_THREAD
            else:
                kind = self._EMIT_SYNC
            dispatcher[ev] = (kind, method)
        return dispatcher

    async def _emit(self, ev, ctx, *args):
        """
        Call one handler event honoring its execution model.

        A plain ```def``` runs inline, an
        ```async def``` is awaited and a ```@threaded def``` runs in a worker
        thread. Exceptions propagate to the caller; the dialog loop translates
        them exactly like the rescue chain. The execution model per event is
        precomputed in ```emit_events```; hot call sites test membership
        first and skip the call entirely when the handler has no
        implementation.

        ### Args

        - **ev** (SmtpdEvent): The event to dispatch
        - **ctx** (SmtpdContext): The per-connection context
        - **args**: Extra positional args (helo/mail/rcpt data, line, ...)

        ### Returns

        - **Any**: The event's return value (a replacement value or None)

        """
        entry = self.emit_events.get(ev)
        if entry is None:
            return None
        kind, method = entry
        if kind == self._EMIT_ASYNC:
            return await method(ctx, *args)
        if kind == self._EMIT_THREAD:
            return await asyncio.to_thread(method, ctx, *args)
        return method(ctx, *args)
