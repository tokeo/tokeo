"""
Runs one or more SMTP receiving services from the application config on top
of the ```tokeo.core.smtpd``` library: per-service events handlers, listener
lists, process topology with pre-forked workers and machine-wide limits.

### Features

- ```smtpd serve [names... | --all]``` starts the configured services of one
    invocation; Ctrl-C or SIGTERM stops everything it started

- ```smtpd list``` prints the configured services

- Per-service worker groups via the ```pre_fork``` option, sharing their
    listeners with SO_REUSEPORT

- Machine-wide ```max_connections``` / ```max_processings``` across all
    services and worker processes of the invocation

### Example

```python
from tokeo.ext.smtpd import TokeoSmtpdEvents

class MailHandler(TokeoSmtpdEvents):
    def on_message_data_event(self, ctx):
        path = ctx.message.spooler.keep('/var/mail/inbound.eml')
        self.app.log.info(f'received {path}')
```

```shell
tokeo smtpd list
tokeo smtpd serve --all
tokeo smtpd serve mx1 mx3
```

### Notes

: Process topology of one invocation: a service without ```pre_fork``` runs
    in the main process loop; ```pre_fork: N``` (N >= 1) gives the service N
    dedicated worker processes; note that ```pre_fork: 1``` here means "an
    own process".
    With only pre-forked services the main process just supervises. A single
    service is optimized: ```pre_fork <= 1``` runs directly in the main
    process without forking, ```pre_fork: N > 1``` runs the main process as
    worker #1 plus N-1 forks

: With a global max set and any forked worker, the counters live in shared
    memory and the limits are exact across all processes OF THIS INVOCATION.
    Per-service limits of a multi-worker group count per worker: for a group
    total of X over N workers configure X/N. Services started by separate
    invocations do not share limits

: A worker killed together with a crashed master relies on the Linux
    parent-death signal; on other platforms orphaned workers keep running
    until stopped manually (the regular SIGTERM shutdown is unaffected)

: One invocation is one unit: start it, stop it (Ctrl-C/SIGTERM), restart it
    (which re-reads the config). For individual control run one invocation
    per service, e.g. ```tokeo smtpd serve mx1```. Starting the same service
    twice fails at the port bind

: Without a configured certificate every process generates its own
    self-signed test certificate, so the workers of a service present
    different ones; configure ```tls_cert_path``` or ```tls_cert``` for one
    certificate in all processes

: The servers log to the application log (```app.log```), its level comes
    from the log config of the application; a ```logger_severity``` in the
    ```server_options``` of a service is left out

: Every selected service is built before a worker forks, so a broken config
    ends the start with the name of the service and nothing is bound. A
    worker that ends on its own, e.g. as it cannot bind its port, ends the
    whole invocation with an error; a restart is left to the supervisor of
    the system, e.g. systemd with ```Restart=on-failure```

"""

import os
import sys
import time
import ctypes
import signal
import asyncio
import multiprocessing
from os.path import basename
from cement.core.meta import MetaMixin
from cement import ex
from tokeo.core.exc import TokeoError
from tokeo.core.utils.modules import resolve_object
from tokeo.core.utils.validate import is_int_ge
from tokeo.core.smtpd import SmtpdServer, GlobalLimits, SmtpdEvents
from tokeo.ext.argparse import Controller
from tokeo.core.cement.service import start_service


class TokeoSmtpdError(TokeoError):
    """Exception class for smtpd-extension-related errors."""

    pass


class TokeoSmtpdEvents(SmtpdEvents):
    """
    Base class for the smtpd events handlers of a Tokeo application.

    Extends the core ```SmtpdEvents``` with the application object: the
    handler receives ```app``` on init and can reach every app service
    (config, log, dramatiq, caches, ...) from any event via ```self.app```.
    The configured ```events_handler``` of a service subclasses this.

    ### Args

    - **app** (Application): The Cement application instance
    - **options** (dict, optional): The service's ```options``` block, handed
        through unevaluated by the extension for the handler to read

    """

    def __init__(self, app, options=None):
        super().__init__(options=options)
        #: The Tokeo application object
        self.app = app


def _resolve_events_handler(path):
    """
    Import the events handler class from its module:class path.

    ### Args

    - **path** (str): The class as module:class, e.g.
        ```myapp.core.smtpd:MailHandler```

    ### Returns

    - **type**: The imported class

    ### Raises

    - **TokeoModulesLoadError**: If the path does not resolve to an object
    - **TokeoSmtpdError**: On a class that does not subclass
        ```TokeoSmtpdEvents```

    """
    events_handler = resolve_object(path)
    if not (isinstance(events_handler, type) and issubclass(events_handler, TokeoSmtpdEvents)):
        raise TokeoSmtpdError(f'smtpd: events_handler {path!r} must subclass tokeo.ext.smtpd.TokeoSmtpdEvents')
    return events_handler


def _build_plan(services, has_global_limit):
    """
    Decide the process topology for one invocation.

    ### Args

    - **services** (list): The service config dicts to start
    - **has_global_limit** (bool): Whether a global limit is configured

    ### Returns

    - **dict**: ```inloop``` (services for the main process loop),
        ```forks``` (list of (service, worker count) tuples),
        ```reuse``` (service names whose listeners need SO_REUSEPORT),
        ```shared``` (True when the limits must span processes)

    ### Notes

    : Implements the topology rules from the module notes, including the
        single-service optimization (main process serves as worker #1)

    """
    plan = {'inloop': [], 'forks': [], 'reuse': set(), 'shared': False}
    if len(services) == 1:
        svc = services[0]
        count = _get_pre_fork(svc)
        plan['inloop'] = [svc]
        if count > 1:
            plan['forks'] = [(svc, count - 1)]
            plan['reuse'] = {svc.get('name')}
            plan['shared'] = has_global_limit
        return plan
    for svc in services:
        count = _get_pre_fork(svc)
        if count == 0:
            plan['inloop'].append(svc)
        else:
            plan['forks'].append((svc, count))
            if count > 1:
                plan['reuse'].add(svc.get('name'))
    plan['shared'] = has_global_limit and bool(plan['forks'])
    return plan


#: ```prctl``` option number for the parent-death signal (Linux ```<sys/prctl.h>```)
_PR_SET_PDEATHSIG = 1


def _set_pdeathsig(sig=signal.SIGTERM):
    """
    Arm the parent-death signal for the calling process (Linux only).

    ### Args

    - **sig** (int): Signal to receive on parent death (default ```SIGTERM```)

    ### Returns

    - **bool**: True if the signal was armed, False on non-Linux or on error

    ### Notes

    : On Linux, asks the kernel (```prctl(PR_SET_PDEATHSIG, sig)```) to send
        ```sig``` to this process when its parent dies, so an orphaned worker
        does not linger if the master crashes without forwarding a stop

    : A no-op on non-Linux platforms and best-effort on error; the master's
        explicit signalling stays the primary shutdown path

    : ```ctypes.CDLL(None)``` resolves ```prctl``` from the C library already
        loaded into the running process, so no library filename is hardcoded
        (```libc.so``` is often a linker script and will not ```dlopen```)

    """
    if not sys.platform.startswith('linux'):
        return False
    try:
        ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_PDEATHSIG, sig, 0, 0, 0)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def _get_pre_fork(svc):
    """Read the pre_fork worker count of a service config (default 0)."""
    count = (svc.get('server_options') or {}).get('pre_fork') or 0
    if not is_int_ge(count, 0):
        raise TokeoSmtpdError(f"smtpd: service '{svc.get('name')}': pre_fork must be an integer of 0 or more, not {count!r}")
    return count


class SharedGlobalLimits(GlobalLimits):
    """
    Global limits with counters shared across worker processes.

    Created in the master BEFORE the workers fork, so every process inherits
    the same shared-memory counters and the limits are exact machine-wide for
    the invocation.

    ### Args

    - **max_processings** (int|None): Global max of concurrent processings
        (None/0 = unlimited)
    - **max_connections** (int|None): Global max of open connections
        (None/0 = unlimited)

    ### Notes

    : The wake event stays per process (an event cannot cross a fork), so
        ```wait_for_slot``` re-checks the shared counters at a short interval, so
        a slot released in another process is seen at most 50 ms later

    """

    def __init__(self, max_processings=None, max_connections=None):
        #: Global max of simultaneous processings, None is unlimited
        self.max_processings = int(max_processings) if max_processings else None
        #: Global max of open connections, None is unlimited
        self.max_connections = int(max_connections) if max_connections else None
        #: @private Wake event of the waiters of this process, set when a
        #: slot may be free
        self.slot_free = asyncio.Event()
        self.slot_free.set()
        self._shared_connection_cnt = multiprocessing.Value('i', 0)
        self._shared_processing_cnt = multiprocessing.Value('i', 0)

    @property
    def connection_cnt(self):
        """Currently open connections over all processes."""
        return self._shared_connection_cnt.value

    @property
    def processing_cnt(self):
        """Currently processed messages over all processes."""
        return self._shared_processing_cnt.value

    def admit(self):
        """Count one accepted connection."""
        with self._shared_connection_cnt.get_lock():
            self._shared_connection_cnt.value += 1

    def release(self):
        """Drop one connection."""
        with self._shared_connection_cnt.get_lock():
            self._shared_connection_cnt.value = max(0, self._shared_connection_cnt.value - 1)

    def begin_processing(self):
        """Count one in-progress dialog."""
        with self._shared_processing_cnt.get_lock():
            self._shared_processing_cnt.value += 1

    def try_begin_processing(self):
        """
        Count one in-progress dialog if max_processings allows it, under one lock.

        ### Returns

        - **bool**: True when the dialog was counted, False at max_processings

        ### Notes

        : Check and count under the same lock, so two processes cannot both
            take the last slot

        """
        with self._shared_processing_cnt.get_lock():
            if self.max_processings is not None and self._shared_processing_cnt.value >= self.max_processings:
                return False
            self._shared_processing_cnt.value += 1
            return True

    def end_processing(self):
        """Drop one in-progress dialog."""
        with self._shared_processing_cnt.get_lock():
            self._shared_processing_cnt.value = max(0, self._shared_processing_cnt.value - 1)

    async def wait_for_slot(self):
        """Wait for a slot with the cross-process re-check interval."""
        try:
            await asyncio.wait_for(self.slot_free.wait(), 0.05)
        except asyncio.TimeoutError:
            pass


class TokeoSmtpd(MetaMixin):
    """
    Extension object driving the configured SMTP receiving services.

    Reads the ```smtpd``` config section, builds one ```SmtpdServer``` per
    service and runs the process topology of one invocation (see the module
    notes). Available as ```app.smtpd```.

    ### Args

    - **app** (Application): The Cement application instance
    - **args**: Variable length argument list passed to parent initializer

    ### Keyword Args

    - **kw**: Arbitrary keyword arguments passed to parent initializer

    """

    class Meta:
        """Extension meta-data."""

        # Label of this extension
        label = 'tokeo.smtpd'

        # Name of the config section
        config_section = 'smtpd'

        # Defaults of the config section
        config_defaults = dict(
            # global max of simultaneous processings of all services, None
            # is unlimited
            max_processings=None,
            # global max of open connections of all services, None is
            # unlimited
            max_connections=None,
            # bytes of the random session id of each connection
            session_id_bytes=8,
            # the services, each with its name, events_handler and server_options
            services=[],
        )

    #: (Application) The Cement application instance, set on setup
    app = None

    def __init__(self, app, *args, **kw):
        super().__init__(*args, **kw)
        self._setup(app)

    def _setup(self, app):
        """Bind the app and merge the config defaults."""
        self.app = app
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)

    def _config(self, key):
        """Read a key from the extension's config section."""
        return self.app.config.get(self._meta.config_section, key)

    def select_services(self, names=None):
        """
        Select the configured services to run.

        ### Args

        - **names** (list, optional): Service names; None selects all

        ### Returns

        - **list**: The selected service config dicts (config order for all,
            given order for a selection)

        ### Raises

        - **TokeoSmtpdError**: Without any configured service, on names that
            do not exist and on names given more than once

        """
        configured_services = self._config('services') or []
        if not configured_services:
            raise TokeoSmtpdError('smtpd: no services configured')
        if not names:
            return list(configured_services)
        by_name = {svc.get('name'): svc for svc in configured_services}
        missing_names = [name for name in names if name not in by_name]
        if missing_names:
            raise TokeoSmtpdError(f'smtpd: unknown service(s): {", ".join(missing_names)}')
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise TokeoSmtpdError(f'smtpd: service(s) named more than once: {", ".join(duplicates)}')
        return [by_name[name] for name in names]

    def _build_listeners(self, svc, reuse_port):
        """The service listeners, flagged for SO_REUSEPORT when needed."""
        listeners = []
        for lst in svc.get('listeners') or []:
            lst = dict(lst)
            if reuse_port:
                lst['reuse_port'] = True
            listeners.append(lst)
        return listeners

    def _build_server(self, svc, global_limits):
        """
        Build the ```SmtpdServer``` for one service config.

        ### Raises

        - **TokeoSmtpdError**: On an error of the service config or of the
            system (address, certificate file), naming the service

        """
        try:
            events_handler = _resolve_events_handler(svc.get('events_handler'))(self.app, options=svc.get('options'))
            # hand the listener hosts to the server so a self-signed certificate
            # derives its CN and SAN from them
            hosts = list(dict.fromkeys(lst.get('host') for lst in svc.get('listeners') or [] if lst.get('host')))
            # the server logs to app.log, the level of the app log config applies
            server_options = {key: value for key, value in (svc.get('server_options') or {}).items() if key != 'logger_severity'}
            return SmtpdServer(
                events_handler,
                options=server_options,
                global_limits=global_limits,
                logger=self.app.log,
                debug=self.app.debug,
                hosts=hosts or None,
                session_id_bytes=self._config('session_id_bytes'),
            )
        except (TokeoError, OSError) as err:
            # config errors (SmtpdError, handler class) and errors of the system
            # (address, certificate file) name the service, a code error passes
            error = str(err).removeprefix('smtpd: ')
            raise TokeoSmtpdError(f"smtpd: service '{svc.get('name')}': {error}") from err

    async def _serve_async(self, services, global_limits, workers, reuse):
        """
        Serve the given services in this process until a stop signal.

        Starts one server per service, waits for SIGINT/SIGTERM, forwards
        the stop to the child workers first and then drains the own servers
        (2 seconds for running dialogs). With an empty service list this is
        the supervising master: it just waits for the signal. A worker that
        ends on its own stops all services, the error names it.

        ### Raises

        - **TokeoSmtpdError**: When a worker ended before the stop signal

        """
        servers = []
        worker_errors = []
        try:
            # the stop handlers come first, a stop during the start is kept
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            # a forked worker starts with the stop signals blocked (see serve)
            signal.pthread_sigmask(signal.SIG_UNBLOCK, (signal.SIGINT, signal.SIGTERM))
            for svc in services:
                server = self._build_server(svc, global_limits)
                listeners = self._build_listeners(svc, svc.get('name') in reuse)
                await server.start(listeners)
                # the server logs its addresses, here the service and its process
                self.app.log.info(f"smtpd: service '{svc.get('name')}' started (pid {os.getpid()})")
                servers.append(server)

            def check_workers():
                # a worker that ends before the stop signal ends all services
                if stop.is_set():
                    return
                for pid, name in workers.items():
                    try:
                        worker_pid, status = os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        continue
                    if worker_pid:
                        worker_errors.append(f"worker {pid} of service '{name}' ended with {os.waitstatus_to_exitcode(status)}")
                        stop.set()

            if workers:
                loop.add_signal_handler(signal.SIGCHLD, check_workers)
                # a worker may have ended before the handler was set
                check_workers()
            await stop.wait()
            for pid in workers:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        finally:
            # a failed start must not leave already started services bound
            for server in servers:
                await server.stop(wait_seconds_before_close=2.0)
        if worker_errors:
            raise TokeoSmtpdError(f'smtpd: {", ".join(worker_errors)}, all services stopped')

    def _run_worker(self, svc, global_limits, reuse):
        """Run one forked worker serving exactly one service."""
        asyncio.run(self._serve_async([svc], global_limits, {}, reuse))

    def serve(self, names=None):
        """
        Run the selected services with the invocation's process topology.

        Blocks until Ctrl-C or SIGTERM, then stops every worker and server
        it started. See the module notes for the topology rules.

        ### Args

        - **names** (list, optional): Service names; None runs all

        """
        services = self.select_services(names)
        max_processings = self._config('max_processings')
        max_connections = self._config('max_connections')
        has_global_limit = bool(max_processings) or bool(max_connections)
        plan = _build_plan(services, has_global_limit)
        if plan['shared']:
            global_limits = SharedGlobalLimits(max_processings, max_connections)
        elif has_global_limit:
            global_limits = GlobalLimits(max_processings, max_connections)
        else:
            global_limits = None
        # build every service once before the fork: a broken config ends the
        # run here, before a worker starts or a port is bound; a deliberate
        # choice, the time of a second build in each process is the price for
        # forks that start and do not end on a config error
        for svc in services:
            self._build_server(svc, global_limits)
        # fork the worker groups BEFORE any event loop exists; the shared
        # limits above are inherited by every worker
        if plan['forks'] and not sys.platform.startswith('linux'):
            self.app.log.warning('smtpd: no parent-death signal on this platform, a crashed master leaves the workers running')
        master_pid = os.getpid()
        workers = {}
        # a worker keeps the stop signals blocked until its handlers are set,
        # so a stop sent right after the fork stays pending and is not lost
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, (signal.SIGINT, signal.SIGTERM))
        try:
            worker_number = 0
            for svc, count in plan['forks']:
                for _ in range(count):
                    worker_number += 1
                    pid = os.fork()
                    if pid == 0:
                        _set_pdeathsig(signal.SIGTERM)
                        # the master died between fork and prctl, so the parent
                        # death signal can never fire, so end at once
                        if os.getppid() != master_pid:
                            os._exit(0)
                        status = 0
                        try:
                            start_service(self.app, 'smtpd-worker', 'smtpd', worker_number)
                            self._run_worker(svc, global_limits, plan['reuse'])
                        except Exception as err:
                            self.app.log.error(f"smtpd: worker {os.getpid()} of service '{svc.get('name')}' failed: {err}")
                            status = 1
                        finally:
                            # a forked worker must never run the parent's code path
                            os._exit(status)
                    workers[pid] = svc.get('name')
        finally:
            # the master goes on with its own mask
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        child_pids = list(workers)
        if child_pids:
            self.app.log.info(f'smtpd: forked workers {child_pids}')
        try:
            asyncio.run(self._serve_async(plan['inloop'], global_limits, workers, plan['reuse']))
        finally:
            # the normal stop already forwarded SIGTERM; after a failed start
            # the workers still run and need it here before the wait
            for pid in child_pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            # wait with a max of 5 s: a worker gets time to drain, then
            # SIGKILL ends it
            pending_pids = list(child_pids)
            end_time = time.monotonic() + 5.0
            while pending_pids and time.monotonic() < end_time:
                for pid in list(pending_pids):
                    try:
                        worker_pid, _status = os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        worker_pid = pid
                    if worker_pid:
                        pending_pids.remove(pid)
                if pending_pids:
                    time.sleep(0.05)
            for pid in pending_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass

    def show_services(self):
        """Print the configured services."""
        for svc in self._config('services') or []:
            listeners_str = ', '.join(f"{lst.get('host')}:{lst.get('port')}" for lst in svc.get('listeners') or [])
            self.app.print(
                f'{svc.get("name")}  events_handler={svc.get("events_handler")}  listeners=[{listeners_str}]  pre_fork={_get_pre_fork(svc)}'
            )


class TokeoSmtpdController(Controller):
    """
    Cement controller for the smtpd receiving services.

    Two commands: ```serve``` runs services (blocking, stopped by Ctrl-C or
    SIGTERM) and ```list``` prints the configured services.

    """

    class Meta:
        """Controller meta-data."""

        label = 'smtpd'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'manage the smtpd receiving services'
        description = 'Run and inspect the SMTP receiving services defined in the smtpd config section.'
        epilog = f'Example: {basename(sys.argv[0])} smtpd serve --all'

    def _setup(self, app):
        """Cement controller setup hook; delegates to the parent."""
        super()._setup(app)

    @ex(
        help='list the configured smtpd services',
        description='Print the services from the smtpd config section.',
    )
    def list(self):
        """Print the configured services."""
        self.app.smtpd.show_services()

    @ex(
        help='run smtpd receiving services',
        description='Run the named services (or all with --all) until Ctrl-C or SIGTERM.',
        epilog=f'Example: {basename(sys.argv[0])} smtpd serve mx1 mx3',
        arguments=[
            (
                ['names'],
                dict(
                    nargs='*',
                    help='service names to run',
                ),
            ),
            (
                ['--all'],
                dict(
                    action='store_true',
                    help='run all configured services',
                ),
            ),
        ],
    )
    def serve(self):
        """Run the selected services (blocking)."""
        names = self.app.pargs.names
        if self.app.pargs.all:
            if names:
                raise TokeoSmtpdError('smtpd: give service names or --all, not both')
            names = None
        elif not names:
            raise TokeoSmtpdError('smtpd: give service names or --all')
        start_service(self.app, 'smtpd', 'smtpd')
        self.app.smtpd.serve(names)


def tokeo_smtpd_extend_app(app):
    """Attach the extension object as ```app.smtpd```."""
    app.extend('smtpd', TokeoSmtpd(app))


def load(app):
    """
    Load the smtpd extension into the application.

    ### Args

    - **app** (Application): The Cement application instance

    """
    app.handler.register(TokeoSmtpdController)
    app.hook.register('post_setup', tokeo_smtpd_extend_app)
