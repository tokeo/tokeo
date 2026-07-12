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
import asyncio
import inspect
import os
import socket
import ipaddress
from enum import Enum, auto

from tokeo.core.utils.date import utc_now
from .context import SmtpdContext, ServerCtx, EnvelopeCtx, MessageCtx, MessageSpooler
from .tls import TlsTransport, EncryptMode
from .logger import Severity, ForwardingLogger
from .events import SmtpdEvents, SmtpdEvent, SMTPD_EVENT_NAMES


from .exc import (
    SmtpdException,
    SmtpdSignal,
    SmtpdStopServiceException,
    SmtpdIOTimeoutException,
    SmtpdIOBufferOverrunException,
    Smtpd421Exception,
    Smtpd500Exception,
    Smtpd500PipeliningException,
    Smtpd500CrLfSequenceException,
    Smtpd501Exception,
    Smtpd503Exception,
    Smtpd530Exception,
    Smtpd552Exception,
    Smtpd451Exception,
    Tls454Exception,
    Tls530Exception,
)


# default constants
DEFAULT_SMTPD_HOST = '127.0.0.1'
DEFAULT_SMTPD_PORT = 2525
DEFAULT_SMTPD_PRE_FORK = 0
DEFAULT_SMTPD_MAX_PROCESSINGS = 4
DEFAULT_IO_WAITREADABLE_SLEEP = 0.1
DEFAULT_IO_CMD_TIMEOUT = 30
DEFAULT_IO_BUFFER_CHUNK_SIZE = 4 * 1024
DEFAULT_IO_BUFFER_MAX_SIZE = 1 * 1024 * 1024


class CmdSequence(Enum):
    """
    Command sequence states (the ```session[:cmd_sequence]``` symbols).

    An enum with ```auto()```, not strings: like Ruby symbols each state is one
    interned object compared by identity (```is```), so the frequent sequence
    checks are a pointer compare and a typo is a NameError at import, not a
    silent miss. The values themselves are never used.

    """

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


# short module-level aliases for the :CMD_* symbol names
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


class SmtpdSession:
    """
    Per-connection dialog state.

    ### Notes

    : ```__slots__``` keeps the field set closed: a misspelled attribute --
        read or write -- raises ```AttributeError``` instead of silently
        creating a new key like a dict would, and access stays as fast as a
        string-keyed dict (writes faster)

    """

    __slots__ = ('cmd_sequence', 'ctx', 'auth_challenge', 'processing')

    def __init__(self):
        #: Current position in the SMTP command sequence (```CmdSequence```)
        self.cmd_sequence = CMD_HELO
        #: The per-connection ```SmtpdContext``` (built on connection start)
        self.ctx = None
        #: Pending AUTH LOGIN challenge state
        self.auth_challenge = {}
        #: True while this connection holds a processing slot
        self.processing = False


def _parse_ports(ports):
    """
    Split a ```ports``` spec into a list of strings.

    ### Args

    - **ports**: e.g. ```'2525'```, ```'2525, 3535'```, ```'2525:3535, 2525'```

    ### Returns

    - **list**: The port specs (```':'``` ranges kept as one item)

    ### Raises

    - **ValueError**: On a missing or an empty ```''``` port item

    """
    items = str(ports).replace(' ', '').split(',')
    if not items:
        raise ValueError('Missing port(s) to bind service(s) to!')
    if '' in items:
        raise ValueError('Do not use empty value "" for port(s). Please use specific port(s)!')
    return items


def _parse_hosts(hosts):
    """
    Split a ```hosts``` spec into a list.

    ### Args

    - **hosts**: e.g. ```'127.0.0.1'```, ```'127.0.0.1, ::1'```, ```'*'```
        or an already split list of host identifiers

    ### Returns

    - **list**: The host identifiers

    ### Raises

    - **ValueError**: On no hosts or an empty ```''``` inner host item

    """
    if isinstance(hosts, (list, tuple)):
        items = [str(host).strip() for host in hosts]
    else:
        items = str(hosts).replace(' ', '').split(',')
    if not items or items == ['']:
        raise ValueError(
            'No hosts defined! Please use specific hostnames and / or ip_addresses or "*" for wildcard!'
        )
    if '' in items:
        raise ValueError(
            'Detected an empty identifier in given hosts! '
            'Please use specific hostnames and / or ip_addresses or "*" for wildcard!'
        )
    return items


def _resolve_host(host):
    """Resolve a host to its ip addresses (an ip literal maps to itself)."""
    if host == '*':
        ips = []
        for info in socket.getaddrinfo(None, 0, proto=socket.IPPROTO_TCP):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
        return ips or [DEFAULT_SMTPD_HOST]
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

    - **max_processings** (int|None): global concurrent-processing cap
        (None/0 = unlimited)
    - **max_connections** (int|None): global open-connection cap
        (None/0 = unlimited)

    """

    def __init__(self, max_processings=None, max_connections=None):
        self.max_processings = int(max_processings) if max_processings else None
        self.max_connections = int(max_connections) if max_connections else None
        self.active = 0
        self.active_processings = 0
        #: shared wake event: a slot released on ANY service wakes every waiter
        #: (each server with global_limits waits on this instead of a local one)
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

        : The base implementation waits purely on the shared event -- correct
            whenever the counters only change inside this event loop. An
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
    parts, deduplicated, ```'*'``` and empties dropped -- the CN becomes
    ```'localhost.local'``` when the first name is a loopback identifier,
    else the first name itself.

    """
    if cert_cn is not None:
        return cert_cn, cert_san
    sans = list(dict.fromkeys(list(hosts) + [host for host, _port in addresses]))
    sans = [san for san in sans if san and san != '*']
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
    - **options** (dict, optional): The service ```options``` block for the ctx
    - **global_limits** (GlobalLimits, optional): Shared caps across services

    """

    def __init__(self, events_handler, settings=None, *, options=None, global_limits=None, debug=False, **kwargs):
        # accept both the Tokeo settings-dict and the keyword style;
        # keyword args merge into settings, which takes precedence
        settings = dict(settings or {})
        if kwargs.get('tls_mode') is not None:
            settings.setdefault('encrypt_mode', kwargs['tls_mode'])
        for key, value in kwargs.items():
            if value is not None and key not in ('ports', 'hosts', 'tls_mode'):
                settings.setdefault(key, value)
        self.events_handler = events_handler
        self.emit_events = self._build_emit_events_dispatcher(events_handler)
        self.options = options or {}
        self.debug = bool(debug)
        self.global_limits = global_limits
        #: Strong refs to scheduled log tasks (the loop only holds them weakly)
        self._pending_log_tasks = set()
        #: The serving loop, captured at start() for thread-safe log scheduling
        self._asyncio_running_loop = None
        #: The exposed logger; forwards to the handler's on_logging_event
        self.logger = ForwardingLogger(self._log_bridge)
        events_handler.log = self.logger
        # ports / hosts / addresses
        self.ports = _parse_ports(kwargs.get('ports') if kwargs.get('ports') is not None else settings.get('ports', DEFAULT_SMTPD_PORT))
        self.hosts = _parse_hosts(kwargs.get('hosts') if kwargs.get('hosts') is not None else settings.get('hosts', DEFAULT_SMTPD_HOST))
        self._addresses = _build_addresses(self.ports, self.hosts)
        self.pre_fork = settings.get('pre_fork', DEFAULT_SMTPD_PRE_FORK)
        # dialog options
        self.io_waitreadable_sleep = settings.get('io_waitreadable_sleep', DEFAULT_IO_WAITREADABLE_SLEEP)
        self.io_cmd_timeout = settings.get('io_cmd_timeout', DEFAULT_IO_CMD_TIMEOUT)
        self.io_buffer_chunk_size = settings.get('io_buffer_chunk_size', DEFAULT_IO_BUFFER_CHUNK_SIZE)
        self.io_buffer_max_size = settings.get('io_buffer_max_size', DEFAULT_IO_BUFFER_MAX_SIZE)
        self.crlf_mode = _parse_mode(CrlfMode, settings.get('crlf_mode'), 'CRLF_ENSURE', 'crlf_mode')
        self.pipelining_extension = bool(settings.get('pipelining_extension', False))
        self.proxy_extension = bool(settings.get('proxy_extension', False))
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
        self.max_processings = settings.get('max_processings', DEFAULT_SMTPD_MAX_PROCESSINGS)
        self.max_connections = settings.get('max_connections', None)
        # validation
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
        handler event itself -- it hands the ```_emit``` coroutine to the
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
        if listeners is None:
            listeners = self._default_listeners()
        for lst in listeners:
            server = await asyncio.start_server(
                self._on_connection,
                host=lst.get('host', '127.0.0.1'),
                port=int(lst.get('port', 2525)),
                limit=int(self.io_buffer_max_size),
                reuse_port=bool(lst.get('reuse_port', False)),
            )
            self._servers.append(server)

    async def _serve_forever(self):
        """Serve all bound listeners until cancelled."""
        try:
            await asyncio.gather(*(s.serve_forever() for s in self._servers))
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
        - **gracefully** (bool): When True, set the shutdown flag and let each
            active dialog end after its current command; when False, drop
            connections immediately

        ### Notes

        : Stop accepting first, then either drain within
            the time budget or force the remaining sessions down with
            ```SmtpdStopConnectionException```

        """
        # always stop accepting new connections first
        self.close()
        if gracefully:
            # signal every active dialog to end after its current command
            self._shutdown = True
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
        except (SmtpdStopServiceException, asyncio.CancelledError):
            # service shutdown / forced drop: end this connection
            pass
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

                ctx.server.local_response = f'{ctx.server.local_host} says welcome!'
                ctx.server.helo_response = f'{ctx.server.local_host} at your service!'

                # check if we want to let this remote station connect us
                if SmtpdEvent.ON_CONNECT_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_CONNECT_EVENT, ctx)

                # drop connection (respond 421) if too busy
                if self._too_many_connections():
                    raise Smtpd421Exception('Abort connection while too busy, exceeding max_connections!')

                # when processings exceed the maximum allowed, wait for a free slot
                await self._acquire_processing_slot(session)

                output = f'220 {ctx.server.local_response.strip()}'

                # log and show to client
                if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.DEBUG, f'>>> {output}')

                writer.write(output.encode('utf-8', 'surrogateescape') + b'\r\n')
                await writer.drain()

                # initialize \r\n line_break for CRLF_ENSURE and CRLF_STRICT
                line_break = b'\r\n'

                while True:
                    if session.cmd_sequence is CMD_STARTTLS:
                        # discard plaintext buffered before the handshake so
                        # commands injected right after STARTTLS cannot run over
                        # the encrypted channel (CVE-2011-0411 class)
                        buffered = getattr(reader, '_buffer', None)
                        if buffered:
                            del buffered[:]
                        await writer.start_tls(self.tls.context)
                        ctx.server.encrypted = utc_now()
                        # client must re-HELO; HELO state dropped (RFC 3207)
                        session.cmd_sequence = CMD_HELO
                        ctx.server.helo = ''

                    raw = await self._read_line(reader)
                    if raw == b'':
                        # connection closed by the remote peer
                        break

                    try:
                        # pipelining extension or violation
                        if not (
                            (session.cmd_sequence is CMD_DATA)
                            or self.pipelining_extension
                            or (self.proxy_extension and session.cmd_sequence is CMD_HELO)
                            or not self._buffered_line_lf(reader)
                        ):
                            raise Smtpd500PipeliningException

                        if self.crlf_mode is CrlfMode.CRLF_ENSURE:
                            line = raw.replace(b'\r', b'').replace(b'\n', b'')
                        elif self.crlf_mode is CrlfMode.CRLF_LEAVE:
                            line_break = raw[-2:] if raw.endswith(b'\r\n') else (raw[-1:] if raw.endswith(b'\n') else b'')
                            if session.cmd_sequence is CMD_DATA:
                                ctx.message.crlf = line_break or b'\r\n'
                            line = raw[: len(raw) - len(line_break)] if line_break else raw
                        elif self.crlf_mode is CrlfMode.CRLF_STRICT:
                            if not raw.endswith(b'\r\n'):
                                raise Smtpd500CrLfSequenceException
                            line = raw[:-2]
                            if b'\r' in line:
                                raise Smtpd500Exception('Line contains additional CR chars!')
                        else:
                            raise Smtpd500Exception(f'Unknown crlf_mode {self.crlf_mode}!')

                        # log the received command line (never the DATA payload);
                        # the DATA check first: it skips the membership test and
                        # the repr()/f-string for every body line
                        if session.cmd_sequence is not CMD_DATA and SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                            await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.DEBUG, f'<<< {line!r}')

                        output = await self.process_line(session, line, line_break)

                    # defined abort channel exception
                    except Smtpd421Exception:
                        # re-raise to exit the loop and drop the connection
                        raise

                    # internal control-flow signals pass to the outer handler
                    except SmtpdSignal:
                        raise

                    except SmtpdException as e:
                        ctx.server.exceptions += 1
                        ctx.server.errors.append(e)
                        if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                            await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.ERROR, f'{e} ({type(e).__name__})', e)
                        output = e.smtp_response

                    # any other error maps to a 500
                    except Exception as e:  # noqa: B902
                        ctx.server.exceptions += 1
                        ctx.server.errors.append(e)
                        if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                            await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.ERROR, f'{e} ({type(e).__name__})', e)
                        output = Smtpd500Exception().smtp_response

                    if output:
                        if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                            await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.DEBUG, f'>>> {output}')

                        writer.write(output.encode('utf-8', 'surrogateescape') + b'\r\n')
                        await writer.drain()

                    if session.cmd_sequence is CMD_QUIT:
                        break

                    # a graceful shutdown ends the dialog after the current command
                    if self._shutdown:
                        break

                output = '221 Service closing transmission channel'
                writer.write(output.encode() + b'\r\n')
                await writer.drain()

            except (asyncio.IncompleteReadError, ConnectionError) as e:
                if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                    await self._emit(
                        SmtpdEvent.ON_LOGGING_EVENT, session.ctx, Severity.DEBUG, 'Connection lost due abort by client! (EOFError)', e
                    )

            except SmtpdStopServiceException:
                # service shutdown: propagate after the disconnect event
                raise

            # any other error drops the connection with a 421
            except Exception as e:  # noqa: B902
                ctx = session.ctx
                ctx.server.exceptions += 1
                ctx.server.errors.append(e)
                if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.ERROR, f'{e} ({type(e).__name__})', e)
                # power down connection with smtp abort return code 421
                try:
                    writer.write(Smtpd421Exception().smtp_response.encode() + b'\r\n')
                    await writer.drain()
                except OSError as e2:
                    if SmtpdEvent.ON_LOGGING_EVENT in self.emit_events:
                        await self._emit(SmtpdEvent.ON_LOGGING_EVENT, ctx, Severity.DEBUG, "Can't send 421 abort code! (IOError)", e2)

        finally:
            # event for cleanup at end of communication
            if SmtpdEvent.ON_DISCONNECT_EVENT in self.emit_events:
                await self._emit(SmtpdEvent.ON_DISCONNECT_EVENT, session.ctx)
            try:
                writer.close()
            except OSError:
                pass
            # finalize the session at least here, drop maybe interrupted parts
            self.process_reset_session(session)

    async def _read_line(self, reader):
        """
        Read one raw line (with terminator) enforcing timeout and buffer size.

        ### Raises

        - **SmtpdIOTimeoutException**: No line within io_cmd_timeout
        - **SmtpdIOBufferOverrunException**: Buffer above io_buffer_max_size;
            both bubble to the outer handler which answers 421

        """
        try:
            # the timeout guards *waiting on the wire*: when a complete line is
            # already buffered, readuntil returns without waiting, so the costly
            # wait_for wrapper (timer + context per call) is skipped -- in
            # pipelined/DATA bulk traffic that is nearly every line
            if self.io_cmd_timeout and not self._buffered_line_lf(reader):
                raw = await asyncio.wait_for(reader.readuntil(b'\n'), self.io_cmd_timeout)
            else:
                raw = await reader.readuntil(b'\n')
        except asyncio.TimeoutError:
            raise SmtpdIOTimeoutException
        except asyncio.LimitOverrunError:
            raise SmtpdIOBufferOverrunException
        except asyncio.IncompleteReadError as err:
            return bytes(err.partial or b'')
        if self.io_buffer_max_size and len(raw) > int(self.io_buffer_max_size):
            raise SmtpdIOBufferOverrunException
        return raw

    @staticmethod
    def _buffered_line_lf(reader):
        """io_buffer_line_lf: is a further complete line already buffered?"""
        return b'\n' in getattr(reader, '_buffer', b'')

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

    async def process_line(self, session, line, line_break):
        """
        Process one dialog line (the ported process_line dispatcher).

        ### Args

        - **session** (dict): The session hash (cmd_sequence,
            auth_challenge, ctx)
        - **line** (bytes): The line after crlf_mode handling (no terminator)
        - **line_break** (bytes): The line break to append in DATA mode

        ### Returns

        - **str**: The SMTP response, or '' for silence (DATA lines, QUIT, PROXY)

        """
        ctx = session.ctx

        if session.cmd_sequence is CMD_AUTH_PLAIN_VALUES:
            # handle authentication
            return await self.process_auth_plain(session, line.decode('utf-8', 'surrogateescape'))

        elif session.cmd_sequence is CMD_AUTH_LOGIN_USER:
            # handle authentication
            return self.process_auth_login_user(session, line.decode('utf-8', 'surrogateescape'))

        elif session.cmd_sequence is CMD_AUTH_LOGIN_PASS:
            # handle authentication
            return await self.process_auth_login_pass(session, line.decode('utf-8', 'surrogateescape'))

        elif session.cmd_sequence is not CMD_DATA:
            # commands are text; decode byte-exact
            line = line.decode('utf-8', 'surrogateescape')

            # Handle specific messages from the client
            if re.match(r'^(HELO|EHLO)(\s+.*)?$', line, re.IGNORECASE):
                # HELO/EHLO
                if session.cmd_sequence is not CMD_HELO:
                    raise Smtpd503Exception
                cmd_data = re.sub(r'^(HELO|EHLO)\ ', '', line, flags=re.IGNORECASE).strip()
                if SmtpdEvent.ON_HELO_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_HELO_EVENT, ctx, cmd_data)
                ctx.server.helo = cmd_data
                session.cmd_sequence = CMD_RSET
                if re.match(r'^EHLO', line, re.IGNORECASE):
                    return (
                        f'250-{ctx.server.helo_response.strip()}\r\n'
                        # respond with 8BITMIME extension
                        + ('250-8BITMIME\r\n' if self.internationalization_extensions else '')
                        # respond with SMTPUTF8 extension
                        + ('250-SMTPUTF8\r\n' if self.internationalization_extensions else '')
                        # respond with PIPELINING if enabled
                        + ('250-PIPELINING\r\n' if self.pipelining_extension else '')
                        # respond with AUTH extensions if enabled
                        + ('250-AUTH LOGIN PLAIN\r\n' if self._auth_available(ctx) else '')
                        # respond with STARTTLS if available and not already enabled
                        + ('' if self.encrypt_mode is EncryptMode.TLS_FORBIDDEN or ctx.server.encrypted else '250-STARTTLS\r\n')
                        + '250 OK'
                    )
                else:
                    return f'250 OK {ctx.server.helo_response.strip()}'.strip()

            elif self.proxy_extension and re.match(r'^PROXY(\s+)', line, re.IGNORECASE):
                # PROXY
                # Docs: haproxy/doc/proxy-protocol.txt (github.com/haproxy)
                # syntax
                # PROXY PROTO source-ip dest-ip source-port dest-port
                if session.cmd_sequence is not CMD_HELO:
                    raise Smtpd503Exception
                if not re.match(
                    r'^PROXY(\s+)(UNKNOWN(|(\s+).*)|TCP(4|6)(\s+)([0-9a-f.:]+)(\s+)([0-9a-f.:]+)(\s+)([0-9]+)(\s+)([0-9]+)(\s*))$',
                    line,
                    re.IGNORECASE,
                ):
                    raise Smtpd421Exception('Abort connection while illegal PROXY command!')
                if ctx.server.proxy:
                    raise Smtpd421Exception('Abort connection while PROXY already set!')
                cmd_data = re.sub(r'^PROXY\ ', '', line, flags=re.IGNORECASE).strip().split()
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
                        if not (1 <= proxy_data['source_port'] <= 65535 and 1 <= proxy_data['dest_port'] <= 65535):
                            raise ValueError
                        # normalize ip addresses
                        proxy_data['source_ip'] = str(proxy_data['source_ip'])
                        proxy_data['source_host'] = proxy_data['source_ip']
                        proxy_data['dest_ip'] = str(proxy_data['dest_ip'])
                        proxy_data['dest_host'] = proxy_data['dest_ip']
                    except (ValueError, IndexError):
                        # change exception into Smtpd exception and drop connection
                        raise Smtpd421Exception('Abort connection for unsupported PROXY parameters!')
                if SmtpdEvent.ON_PROXY_EVENT in self.emit_events:
                    return_value = await self._emit(SmtpdEvent.ON_PROXY_EVENT, ctx, proxy_data)
                    if return_value:
                        proxy_data = return_value
                ctx.server.proxy = proxy_data
                # otherwise on buffering clients or enabled feature pipelining
                # the original client will receive unhandleable responses
                return ''

            elif re.match(r'^STARTTLS\s*$', line, re.IGNORECASE):
                # STARTTLS
                if self.encrypt_mode is EncryptMode.TLS_FORBIDDEN:
                    raise Smtpd500Exception
                if session.cmd_sequence is CMD_HELO:
                    raise Smtpd503Exception
                if not self.tls:
                    raise Tls454Exception
                if ctx.server.encrypted:
                    raise Smtpd503Exception
                session.cmd_sequence = CMD_STARTTLS
                # return with new service ready message
                return '220 Ready to start TLS'

            elif re.match(r'^AUTH(\s+)((LOGIN|PLAIN)(\s+[A-Z0-9=]+)?|CRAM-MD5)\s*$', line, re.IGNORECASE):
                # AUTH
                if self.auth_mode is AuthMode.AUTH_FORBIDDEN:
                    raise Smtpd500Exception
                if session.cmd_sequence is not CMD_RSET:
                    raise Smtpd503Exception
                if not self._auth_available(ctx):
                    raise Tls530Exception
                if ctx.server.authenticated:
                    raise Smtpd503Exception
                # handle command line
                auth_data = re.sub(r'^AUTH\ ', '', line, flags=re.IGNORECASE).strip()
                auth_data = re.sub(r'\s+', ' ', auth_data).split(' ')
                # handle auth command
                if re.search(r'PLAIN', auth_data[0], re.IGNORECASE):
                    if len(auth_data) == 1:
                        session.cmd_sequence = CMD_AUTH_PLAIN_VALUES
                        # response code include post ending with a space
                        return '334 '
                    else:
                        # handle authentication with given auth_id and password
                        return await self.process_auth_plain(session, auth_data[1] if len(auth_data) == 2 else '')

                elif re.search(r'LOGIN', auth_data[0], re.IGNORECASE):
                    if len(auth_data) == 1:
                        # reset auth_challenge
                        session.auth_challenge = {}
                        session.cmd_sequence = CMD_AUTH_LOGIN_USER
                        # response code with request for Username
                        return '334 ' + base64.b64encode(b'Username:').decode()

                    elif len(auth_data) == 2:
                        # handle next sequence
                        return self.process_auth_login_user(session, auth_data[1])

                    else:
                        raise Smtpd500Exception

                # CRAM-MD5 is not supported in case of also unencrypted data
                # delivery; instead of supporting password encryption only, an
                # optional SMTPS service should be provided

                else:
                    # unknown auth method
                    raise Smtpd500Exception

            elif re.match(r'^NOOP\s*$', line, re.IGNORECASE):
                # NOOP
                return '250 OK'

            elif re.match(r'^RSET\s*$', line, re.IGNORECASE):
                # RSET
                if session.cmd_sequence is CMD_HELO:
                    raise Smtpd503Exception
                if self.encrypt_mode is EncryptMode.TLS_REQUIRED and not ctx.server.encrypted:
                    raise Tls530Exception
                self.process_reset_session(session)
                return '250 OK'

            elif re.match(r'^QUIT\s*$', line, re.IGNORECASE):
                # QUIT
                session.cmd_sequence = CMD_QUIT
                return ''

            elif re.match(r'^MAIL FROM:', line, re.IGNORECASE):
                # MAIL
                if session.cmd_sequence is not CMD_RSET:
                    raise Smtpd503Exception
                if self.encrypt_mode is EncryptMode.TLS_REQUIRED and not ctx.server.encrypted:
                    raise Tls530Exception
                if self.auth_mode is AuthMode.AUTH_REQUIRED and not ctx.server.authenticated:
                    raise Smtpd530Exception
                cmd_data = re.sub(r'^MAIL FROM:', '', line, flags=re.IGNORECASE).strip()
                if re.search(r'\sBODY=7BIT(\s|$)', cmd_data, re.IGNORECASE):
                    if not self.internationalization_extensions:
                        raise Smtpd501Exception
                    ctx.envelope.encoding_body = '7bit'
                elif re.search(r'\sBODY=8BITMIME(\s|$)', cmd_data, re.IGNORECASE):
                    if not self.internationalization_extensions:
                        raise Smtpd501Exception
                    ctx.envelope.encoding_body = '8bitmime'
                elif re.search(r'\sBODY=.*$', cmd_data, re.IGNORECASE):
                    raise Smtpd501Exception
                if re.search(r'\sSMTPUTF8(\s|$)', cmd_data, re.IGNORECASE):
                    if not self.internationalization_extensions:
                        raise Smtpd501Exception
                    ctx.envelope.encoding_utf8 = 'utf8'
                if self.internationalization_extensions:
                    cmd_data = re.sub(r'\sBODY=(7BIT|8BITMIME)', '', cmd_data, flags=re.IGNORECASE)
                    cmd_data = re.sub(r'\sSMTPUTF8', '', cmd_data, flags=re.IGNORECASE).strip()
                if SmtpdEvent.ON_MAIL_FROM_EVENT in self.emit_events:
                    return_value = await self._emit(SmtpdEvent.ON_MAIL_FROM_EVENT, ctx, cmd_data)
                    if return_value:
                        cmd_data = return_value
                ctx.envelope.mail_from = cmd_data
                session.cmd_sequence = CMD_MAIL
                return '250 OK'

            elif re.match(r'^RCPT TO:', line, re.IGNORECASE):
                # RCPT
                if session.cmd_sequence not in (CMD_MAIL, CMD_RCPT):
                    raise Smtpd503Exception
                if self.encrypt_mode is EncryptMode.TLS_REQUIRED and not ctx.server.encrypted:
                    raise Tls530Exception
                if self.auth_mode is AuthMode.AUTH_REQUIRED and not ctx.server.authenticated:
                    raise Smtpd530Exception
                cmd_data = re.sub(r'^RCPT TO:', '', line, flags=re.IGNORECASE).strip()
                if SmtpdEvent.ON_RCPT_TO_EVENT in self.emit_events:
                    return_value = await self._emit(SmtpdEvent.ON_RCPT_TO_EVENT, ctx, cmd_data)
                    if return_value:
                        cmd_data = return_value
                ctx.envelope.rcpt_tos.append(cmd_data)
                session.cmd_sequence = CMD_RCPT
                return '250 OK'

            elif re.match(r'^DATA\s*$', line, re.IGNORECASE):
                # DATA
                if session.cmd_sequence is not CMD_RCPT:
                    raise Smtpd503Exception
                if self.encrypt_mode is EncryptMode.TLS_REQUIRED and not ctx.server.encrypted:
                    raise Tls530Exception
                if self.auth_mode is AuthMode.AUTH_REQUIRED and not ctx.server.authenticated:
                    raise Smtpd530Exception
                session.cmd_sequence = CMD_DATA
                ctx.message.received = utc_now()
                return '354 Enter message, ending with "." on a line by itself'

            else:
                # If we somehow get to this point then
                # allow handling of unknown command line
                if SmtpdEvent.ON_PROCESS_LINE_UNKNOWN_EVENT in self.emit_events:
                    return_value = await self._emit(SmtpdEvent.ON_PROCESS_LINE_UNKNOWN_EVENT, ctx, line)
                    if return_value:
                        return return_value
                # the default on_process_line_unknown_event raises
                # Smtpd500Exception; raising here keeps the same behaviour: the
                # serve loop counts it, records the error and logs it, and the
                # full 500 wire response is produced
                raise Smtpd500Exception

        else:
            # If we are in data mode then ...

            if not ctx.message.data:
                if SmtpdEvent.ON_MESSAGE_DATA_START_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_MESSAGE_DATA_START_EVENT, ctx)
                # always initialize bytesize on data
                ctx.message.bytesize = len(ctx.message.data)

            # ... and the entire new message data (line) does NOT consist
            # solely of a period (.) on a line by itself then we are being
            # told to continue data mode
            if line != b'.':
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
                    self.process_reset_session(session)
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
            # call event to process message
            try:
                if SmtpdEvent.ON_MESSAGE_DATA_EVENT in self.emit_events:
                    await self._emit(SmtpdEvent.ON_MESSAGE_DATA_EVENT, ctx)
                return '250 Requested mail action okay, completed'

            # test for SmtpdException
            except SmtpdException:
                # just re-raise exception set by app
                raise

            # test all other Exceptions
            except Exception as e:  # noqa: B902 - Ruby: StandardError => 451
                # send correct aborted message to smtp dialog
                raise Smtpd451Exception(str(e))

            finally:
                # always start with empty values after finishing incoming message
                # and rset command sequence
                self.process_reset_session(session)

    # ---- authentication --------------------------------

    @staticmethod
    def _decode64(encoded):
        """
        Decode base64 like Ruby's ```Base64.decode64``` (tolerant).

        Ruby ignores non-alphabet characters and missing padding instead of
        raising; garbage input yields empty/partial bytes and fails later at the
        credentials check (Smtpd500), which this mirrors.

        ### Args

        - **encoded** (str): The base64 text from the dialog

        ### Returns

        - **str**: The decoded value (byte-exact via surrogateescape)

        """
        cleaned = re.sub(r'[^A-Za-z0-9+/=]', '', encoded or '')
        cleaned = cleaned.ljust(len(cleaned) + (-len(cleaned) % 4), '=')
        try:
            raw = base64.b64decode(cleaned, validate=False)
        except ValueError:
            return ''
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
            # check for valid credentials parameters
            if len(auth_values) != 3:
                raise Smtpd500Exception
            # call event function to test credentials
            return_value = await self._emit(SmtpdEvent.ON_AUTH_EVENT, session.ctx, auth_values[0], auth_values[1], auth_values[2])
            if return_value:
                # overwrite data with returned value as authorization id
                auth_values[0] = return_value
            # save authentication information to ctx
            ctx = session.ctx
            ctx.server.authorization_id = auth_values[1] if str(auth_values[0]) == '' else auth_values[0]
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
        # save challenged auth_id
        session.auth_challenge['authorization_id'] = ''
        session.auth_challenge['authentication_id'] = self._decode64(encoded_auth_response)
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
            # check for valid credentials
            return_value = await self._emit(SmtpdEvent.ON_AUTH_EVENT, session.ctx, auth_values[0], auth_values[1], auth_values[2])
            if return_value:
                # overwrite data with returned value as authorization id
                auth_values[0] = return_value
            # save authentication information to ctx
            ctx = session.ctx
            ctx.server.authorization_id = auth_values[1] if str(auth_values[0]) == '' else auth_values[0]
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
            ctx and sets the sequence to CMD_HELO (else CMD_RSET)

        """
        # set active command sequence info
        session.cmd_sequence = CMD_HELO if connection_initialize else CMD_RSET
        # drop any auth challenge
        session.auth_challenge = {}
        # test existing of ctx
        if session.ctx is None:
            # create a new one
            session.ctx = SmtpdContext(options=self.options)
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
        ctx.options = self.options

    # ---- event dispatch -------------------------------------------------------

    #: Dispatch kinds, resolved once per handler class (see the builder below)
    _EMIT_SYNC, _EMIT_ASYNC, _EMIT_THREAD = 0, 1, 2

    def _build_emit_events_dispatcher(self, events_handler):
        """
        Precompute the dispatch table ```SmtpdEvent -> (kind, bound method)```.

        ### Notes

        - Resolves per event *once* what ```_emit``` previously resolved on
            every call (```getattr``` + ```iscoroutinefunction``` + threaded
            marker), cutting the per-event dispatch cost to a dict lookup
        - Events the handler did not override keep NO entry when their base is
            a no-op, so call sites skip ```_emit``` (and its coroutine) entirely
            via ```in self.emit_events```; ```ON_AUTH_EVENT``` always keeps
            an entry because its base carries behaviour (the default deny
            raises 535 when not overridden)

        ### Args

        - **events_handler** (SmtpdEvents): The bound handler instance

        ### Returns

        - **dict**: ```{event_name: (kind, method)}``` for dispatchable events

        """
        dispatcher = {}
        for ev in SmtpdEvent:
            name = SMTPD_EVENT_NAMES[ev]
            method = getattr(events_handler, name, None)
            if method is None:
                continue
            overridden = getattr(type(events_handler), name, None) is not getattr(SmtpdEvents, name, None)
            if not overridden and ev != SmtpdEvent.ON_AUTH_EVENT:
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
        thread. Exceptions propagate to the caller -- the dialog loop translates
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
