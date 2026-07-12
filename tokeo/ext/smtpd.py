"""
Tokeo SMTPD Extension Module.

Runs one or more SMTP receiving services from the application config on top
of the ```tokeo.core.smtpd``` library: per-service events handlers, listener
lists, process topology with pre-forked workers and machine-wide limits.

### Features

- ```smtpd serve [names... | --all]``` starts the configured services of one
    invocation; Ctrl-C or SIGTERM stops everything it started

- ```smtpd list``` prints the configured services

- Per-service worker groups via the ```pre_fork``` setting, sharing their
    listeners with SO_REUSEPORT

- Machine-wide ```max_connections``` / ```max_processings``` across all
    services and worker processes of the invocation

### Notes

: Process topology of one invocation: a service without ```pre_fork``` runs
    in the main process loop; ```pre_fork: N``` (N >= 1) gives the service N
    dedicated worker processes -- note that ```pre_fork: 1``` here means "an
    own process", while the plain server library only forks for values > 1.
    With only pre-forked services the main process just supervises. A single
    service is optimized: ```pre_fork <= 1``` runs directly in the main
    process without forking, ```pre_fork: N > 1``` runs the main process as
    worker #1 plus N-1 forks

: With a global cap set and any forked worker, the counters live in shared
    memory and the caps are exact across all processes OF THIS INVOCATION.
    Per-service caps of a multi-worker group count per worker -- for a group
    total of X over N workers configure X/N. Services started by separate
    invocations do not share limits

: A worker killed together with a crashed master relies on the Linux
    parent-death signal; on other platforms orphaned workers keep running
    until stopped manually (the regular SIGTERM shutdown is unaffected)

: One invocation is one unit: start it, stop it (Ctrl-C/SIGTERM), restart it
    (which re-reads the config). For individual control run one invocation
    per service, e.g. ```tokeo smtpd serve mx1```. Starting the same service
    twice fails at the port bind

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

"""

import os
import sys
import signal
import asyncio
import importlib
import multiprocessing
from os.path import basename
from cement.core.meta import MetaMixin
from cement import ex
from tokeo.core.exc import TokeoError
from tokeo.core.smtpd import SmtpdServer, GlobalLimits, SmtpdEvents
from tokeo.core.smtpd.prefork import set_pdeathsig
from tokeo.ext.argparse import Controller


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

    """

    def __init__(self, app, options=None):
        super().__init__(options=options)
        #: The Tokeo application object
        self.app = app


def _resolve_events_handler(path):
    """
    Import the events handler class from its dotted path.

    ### Args

    - **path** (str): Dotted path, e.g. ```myapp.core.smtpd.MailHandler```

    ### Returns

    - **type**: The imported class

    ### Raises

    - **TokeoSmtpdError**: On a path without module part, on import failure
        and on a class that does not subclass ```TokeoSmtpdEvents```

    """
    module, _, name = str(path).rpartition('.')
    if not module:
        raise TokeoSmtpdError(f'smtpd: events_handler needs a dotted path: {path!r}')
    try:
        events_handler = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as err:
        raise TokeoSmtpdError(f'smtpd: cannot import events_handler {path!r}: {err}')
    if not (isinstance(events_handler, type) and issubclass(events_handler, TokeoSmtpdEvents)):
        raise TokeoSmtpdError(f'smtpd: events_handler {path!r} must subclass tokeo.ext.smtpd.TokeoSmtpdEvents')
    return events_handler


def _plan(services, has_global_limit):
    """
    Decide the process topology for one invocation.

    ### Args

    - **services** (list): The service config dicts to start
    - **has_global_limit** (bool): Whether a global cap is configured

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
        count = _pre_fork(svc)
        plan['inloop'] = [svc]
        if count > 1:
            plan['forks'] = [(svc, count - 1)]
            plan['reuse'] = {svc.get('name')}
            plan['shared'] = has_global_limit
        return plan
    for svc in services:
        count = _pre_fork(svc)
        if count == 0:
            plan['inloop'].append(svc)
        else:
            plan['forks'].append((svc, count))
            if count > 1:
                plan['reuse'].add(svc.get('name'))
    plan['shared'] = has_global_limit and bool(plan['forks'])
    return plan


def _pre_fork(svc):
    """Read the pre_fork worker count of a service config (default 0)."""
    return int((svc.get('settings') or {}).get('pre_fork') or 0)


class SharedGlobalLimits(GlobalLimits):
    """
    Global limits with counters shared across worker processes.

    Created in the master BEFORE the workers fork, so every process inherits
    the same shared-memory counters and the caps are exact machine-wide for
    the invocation.

    ### Notes

    : The wake event stays per process (an event cannot cross a fork), so
        ```slot_wait``` re-checks the shared counters at a short interval --
        a slot released in another process is seen at most 50 ms later

    """

    def __init__(self, max_processings=None, max_connections=None):
        self.max_processings = int(max_processings) if max_processings else None
        self.max_connections = int(max_connections) if max_connections else None
        self.slot_free = asyncio.Event()
        self.slot_free.set()
        self._shared_active = multiprocessing.Value('i', 0)
        self._shared_processings = multiprocessing.Value('i', 0)

    @property
    def active(self):
        """Currently open connections over all processes."""
        return self._shared_active.value

    @property
    def active_processings(self):
        """Currently processed messages over all processes."""
        return self._shared_processings.value

    def admit(self):
        """Count one accepted connection."""
        with self._shared_active.get_lock():
            self._shared_active.value += 1

    def release(self):
        """Drop one connection."""
        with self._shared_active.get_lock():
            self._shared_active.value = max(0, self._shared_active.value - 1)

    def begin_processing(self):
        """Count one in-progress dialog."""
        with self._shared_processings.get_lock():
            self._shared_processings.value += 1

    def end_processing(self):
        """Drop one in-progress dialog."""
        with self._shared_processings.get_lock():
            self._shared_processings.value = max(0, self._shared_processings.value - 1)

    async def slot_wait(self):
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

    """

    class Meta:
        """Extension meta-data and config defaults."""

        #: Unique identifier for this handler
        label = 'tokeo.smtpd'

        #: Configuration section in the application config
        config_section = 'smtpd'

        #: Default configuration values
        config_defaults = dict(
            max_processings=None,
            max_connections=None,
            services=[],
        )

    def __init__(self, app, *args, **kw):
        super(TokeoSmtpd, self).__init__(*args, **kw)
        self._setup(app)

    def _setup(self, app):
        """Bind the app and merge the config defaults."""
        self.app = app
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)

    def _config(self, key):
        """Read a key from the extension's config section."""
        return self.app.config.get(self._meta.config_section, key)

    def services(self, names=None):
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
        configured = self._config('services') or []
        if not configured:
            raise TokeoSmtpdError('smtpd: no services configured')
        if not names:
            return list(configured)
        by_name = {svc.get('name'): svc for svc in configured}
        missing = [name for name in names if name not in by_name]
        if missing:
            raise TokeoSmtpdError(f'smtpd: unknown service(s): {", ".join(missing)}')
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise TokeoSmtpdError(f'smtpd: service(s) named more than once: {", ".join(duplicates)}')
        return [by_name[name] for name in names]

    def _listeners(self, svc, reuse_port):
        """The service listeners, flagged for SO_REUSEPORT when needed."""
        listeners = []
        for lst in svc.get('listeners') or []:
            lst = dict(lst)
            if reuse_port:
                lst['reuse_port'] = True
            listeners.append(lst)
        return listeners

    def _build_server(self, svc, global_limits):
        """Build the ```SmtpdServer``` for one service config."""
        events_handler = _resolve_events_handler(svc.get('events_handler'))(self.app, options=svc.get('options'))
        # hand the listener hosts to the server so a self-signed certificate
        # derives its CN and SAN from them
        hosts = list(dict.fromkeys(lst.get('host') for lst in svc.get('listeners') or [] if lst.get('host')))
        return SmtpdServer(
            events_handler,
            settings=svc.get('settings'),
            options=svc.get('options'),
            global_limits=global_limits,
            debug=self.app.debug,
            hosts=hosts or None,
        )

    async def _serve_async(self, services, global_limits, child_pids, reuse):
        """
        Serve the given services in this process until a stop signal.

        Starts one server per service, waits for SIGINT/SIGTERM, forwards
        the stop to the child workers first and then drains the own servers
        (2 seconds for running dialogs). With an empty service list this is
        the supervising master: it just waits for the signal.

        """
        servers = []
        try:
            for svc in services:
                server = self._build_server(svc, global_limits)
                listeners = self._listeners(svc, svc.get('name') in reuse)
                await server.start(listeners)
                where = ', '.join(f"{lst.get('host')}:{lst.get('port')}" for lst in listeners)
                self.app.log.info(f"smtpd: service '{svc.get('name')}' listening on {where} (pid {os.getpid()})")
                servers.append(server)
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            await stop.wait()
            for pid in child_pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        finally:
            # a failed start must not leave already started services bound
            for server in servers:
                await server.stop(wait_seconds_before_close=2.0)

    def _run_worker(self, svc, global_limits, reuse):
        """Run one forked worker serving exactly one service."""
        asyncio.run(self._serve_async([svc], global_limits, [], reuse))

    def serve(self, names=None):
        """
        Run the selected services with the invocation's process topology.

        Blocks until Ctrl-C or SIGTERM, then stops every worker and server
        it started. See the module notes for the topology rules.

        ### Args

        - **names** (list, optional): Service names; None runs all

        """
        services = self.services(names)
        max_processings = self._config('max_processings')
        max_connections = self._config('max_connections')
        has_global_limit = bool(max_processings) or bool(max_connections)
        plan = _plan(services, has_global_limit)
        if plan['shared']:
            global_limits = SharedGlobalLimits(max_processings, max_connections)
        elif has_global_limit:
            global_limits = GlobalLimits(max_processings, max_connections)
        else:
            global_limits = None
        # fork the worker groups BEFORE any event loop exists; the shared
        # limits above are inherited by every worker
        if plan['forks'] and not sys.platform.startswith('linux'):
            self.app.log.warning('smtpd: no parent-death signal on this platform -- a crashed master leaves the workers running')
        child_pids = []
        for svc, count in plan['forks']:
            for _ in range(count):
                pid = os.fork()
                if pid == 0:
                    set_pdeathsig(signal.SIGTERM)
                    status = 0
                    try:
                        self._run_worker(svc, global_limits, plan['reuse'])
                    except Exception:
                        status = 1
                    finally:
                        # a forked worker must never run the parent's code path
                        os._exit(status)
                child_pids.append(pid)
        if child_pids:
            self.app.log.info(f'smtpd: forked workers {child_pids}')
        try:
            asyncio.run(self._serve_async(plan['inloop'], global_limits, child_pids, plan['reuse']))
        finally:
            # the normal stop already forwarded SIGTERM; after a failed start
            # the workers still run and need it here before the reap
            for pid in child_pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for pid in child_pids:
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass

    def list(self):
        """Print the configured services."""
        for svc in self._config('services') or []:
            where = ', '.join(f"{lst.get('host')}:{lst.get('port')}" for lst in svc.get('listeners') or [])
            self.app.print(f"{svc.get('name')}  events_handler={svc.get('events_handler')}  listeners=[{where}]  pre_fork={_pre_fork(svc)}")


class TokeoSmtpdController(Controller):
    """
    Cement controller for the smtpd receiving services.

    Two commands: ```serve``` runs services (blocking, stopped by Ctrl-C or
    SIGTERM) and ```list``` prints the configured services.

    """

    class Meta:
        """Meta configuration for the smtpd controller."""

        label = 'smtpd'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'manage the smtpd receiving services'
        description = 'Run and inspect the SMTP receiving services defined in the smtpd config section.'
        epilog = f'Example: {basename(sys.argv[0])} smtpd serve --all'

    def _setup(self, app):
        """Cement controller setup hook; delegates to the parent."""
        super(TokeoSmtpdController, self)._setup(app)

    @ex(
        help='list the configured smtpd services',
        description='Print the services from the smtpd config section.',
    )
    def list(self):
        """Print the configured services."""
        self.app.smtpd.list()

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
