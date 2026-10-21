"""
Tokeo Dramatiq extension controller.

The ```dramatiq``` commands of the app: ```serve``` starts the worker
processes from the config and its switches, ```locks``` shows or purges the
diskcache locks of the actors.

"""

import os
import sys
import multiprocessing
from os.path import basename, dirname, abspath
from cement import ex
from dramatiq import cli
from dramatiq import worker as dramatiq_worker
from tokeo.ext.argparse import Controller
from tokeo.ext.dramatiq.base import TokeoDramatiqError, _number


class TokeoDramatiqController(Controller):
    """
    Cement controller that drives Dramatiq workers and locks via CLI.

    Two commands: ```serve``` spawns the configured worker pool (process +
    thread counts, optional --watch path for actor reload during dev) and
    ```locks``` inspects or purges the distributed locks held in diskcache.

    """

    class Meta:
        """Meta configuration for the Dramatiq controller."""

        label = 'dramatiq'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'manage the dramatiq service'
        description = 'Provides command-line interfaces to manage Dramatiq workers, enabling task processing in a distributed system.'
        epilog = f'Example: {basename(sys.argv[0])} dramatiq serve --skip-logging'

    def _setup(self, app):
        """Cement controller setup hook; delegates to the parent."""
        super(TokeoDramatiqController, self)._setup(app)

    def _worker_option(self, option, key, allow_auto=False, config_min_value=1):
        """
        Get a worker option from its switch or else from the config.

        ### Args

        - **option** (str): The name of the parsed command line option
        - **key** (str): The config key of the option
        - **allow_auto** (bool, optional): Whether ```'auto'``` is allowed.
            Defaults to False
        - **config_min_value** (int, optional): The lowest number allowed
            in the config. Defaults to 1

        ### Returns

        - **int|str**: The number as int, or ```'auto'```

        ### Raises

        - **TokeoDramatiqError**: If the switch or the config value is not
            allowed

        """
        # a given switch replaces the config value
        value = getattr(self.app.pargs, option)
        if value is not None:
            return _number(f'switch --{option.replace("_", "-")}', value, allow_auto=allow_auto)
        value = _number(f'dramatiq config {key}', self.app.dramatiq.config(key), min_value=config_min_value, allow_auto=allow_auto)
        # 0 stays valid in the config for prefetch and means auto as before
        return 'auto' if value == 0 else value

    # --------------------------------------------------------------------------------------

    @ex(
        help='access the dramatiq locks from cache',
        description='Handle dramatiq locks stored in diskcache.',
        epilog=f'Use "{basename(sys.argv[0])} dramatiq locks" to handle the dramatiq locks in diskcache.',
        arguments=[
            (
                ['--purge'],
                dict(
                    action='store_true',
                    help='purge the dramatiq locks from cache',
                ),
            ),
        ],
    )
    def locks(self):
        """
        Manage distributed locks used by Dramatiq tasks.

        This command provides access to the distributed locks stored in the
        diskcache. It can display information about the locks configuration or
        purge all locks from the cache when needed.

        ### Notes

        - With --purge: drops all locks from the cache; useful to reset
            after a worker crash that left a stale acquire behind
        - Without arguments: prints the cache tag the locks live under

        """
        # check if parameter for locks cache
        if self.app.pargs.purge:
            num = self.app.dramatiq.locks.purge()
            self.app.print(f'Purged {num} dramatiq locks from cache.')
        else:
            # show the locks info
            self.app.print(f'Dramatiq locks using the cache tag: {self.app.dramatiq.locks._tag}')

    # --------------------------------------------------------------------------------------

    @ex(
        help='spin up the dramatiq service workers',
        description=(
            'Starts Dramatiq workers, allowing for configuration of '
            'worker processes and threads. Optional flags for logging '
            'and file watching.'
        ),
        epilog=(
            f'Use "{basename(sys.argv[0])} dramatiq serve" with options '
            'like "--skip-logging" for custom logging or "--watch" for '
            'automatic actor reloading.'
        ),
        arguments=[
            (
                ['--skip-logging'],
                dict(
                    action='store_true',
                    help='do not call dramatiq logging.basicConfig()',
                ),
            ),
            (
                ['--watch'],
                dict(
                    action='store',
                    help='reload actors on changes and restart workers',
                ),
            ),
            (
                ['--no-purge'],
                dict(
                    action='store_true',
                    help='keep the dramatiq locks on start and on restarts by --watch',
                ),
            ),
            (
                ['-p', '--processes'],
                dict(
                    metavar='N|auto',
                    help='number of worker processes, auto follows the dramatiq defaults',
                ),
            ),
            (
                ['-t', '--threads'],
                dict(
                    metavar='N',
                    help='number of worker threads per process',
                ),
            ),
            (
                ['--queue-prefetch'],
                dict(
                    metavar='N|auto',
                    help='messages to prefetch per queue, auto follows the dramatiq defaults',
                ),
            ),
            (
                ['--delay-queue-prefetch'],
                dict(
                    metavar='N|auto',
                    help='messages to prefetch per delay queue, auto follows the dramatiq defaults',
                ),
            ),
            (
                ['--worker-shutdown-timeout'],
                dict(
                    metavar='MS',
                    help='milliseconds to wait for workers on shutdown',
                ),
            ),
            (
                ['--use-spawn'],
                dict(
                    dest='use_spawn',
                    action='store_const',
                    const=True,
                    default=None,
                    help='start the worker processes by spawn',
                ),
            ),
            (
                ['--use-forkserver'],
                dict(
                    dest='use_spawn',
                    action='store_const',
                    const=False,
                    default=None,
                    help='start the worker processes by forkserver',
                ),
            ),
            (
                ['--queues'],
                dict(
                    nargs='+',
                    metavar='QUEUE',
                    help='consume only these queues, all queues without this option',
                ),
            ),
        ],
    )
    def serve(self):
        """
        Start Dramatiq workers with customizable settings.

        This command starts Dramatiq worker processes that process tasks from the
        message queues. It applies configuration from the application config while
        allowing CLI arguments to override specific settings.

        ### Args

        - **--skip-logging** (flag): Skip Dramatiq's default logging configuration
        - **--watch** (str): Path to watch for file changes to reload actors
        - **--no-purge** (flag): Keep the diskcache locks on start and on
            restarts by --watch
        - **-p, --processes** (int|auto): Number of worker processes
        - **-t, --threads** (int): Number of worker threads per process
        - **--queue-prefetch** (int|auto): Messages to prefetch per queue
        - **--delay-queue-prefetch** (int|auto): Messages to prefetch per
            delay queue
        - **--worker-shutdown-timeout** (int): Milliseconds to wait for the
            workers on shutdown
        - **--use-spawn** (flag): Start the worker processes by spawn
        - **--use-forkserver** (flag): Start the worker processes by
            forkserver
        - **--queues** (str): Names of the queues to consume, all queues
            without this option

        ### Raises

        - **TokeoDramatiqError**: If a worker switch, a worker option or
            ```use_spawn``` in the config, or the env of dramatiq is not
            allowed

        ### Notes

        - Worker configuration includes process count, thread count, shutdown
            timeout, restart delay, and queue prefetch settings
        - Each worker switch replaces its config value (```worker_processes```,
            ```worker_threads```, ```queue_prefetch```, ```delay_queue_prefetch```,
            ```worker_shutdown_timeout```); without the switch the config
            value applies
        - With auto the dramatiq defaults apply: the cpus of the host for the
            processes, threads x 2 and threads x 1000 for the prefetch. The
            cpus of the host ignore a cpu limit of a container
        - In the config the prefetch also takes 0 for auto as before
        - ```worker_poll_interval``` reaches the workers by the env
            ```dramatiq_worker_timeout```, which dramatiq reads on import.
            Without the config value an env set from outside applies, so it
            is checked here; a non number fails earlier, when dramatiq
            gets imported
        - With --watch, the command monitors the given directory for changes
            and reloads actors when files change
        - In debug mode of the app dramatiq logs on debug level as well,
            unless --skip-logging is given
        - With --queues the instance still declares all queues of the
            actors but consumes only the given ones, their delay queues
            included. A wrong name raises no error, the instance then
            consumes nothing of it; a queue no instance consumes stays
            unprocessed

        - Worker processes log to the console; the call blocks until they
            terminate (signal, error or shutdown via the management hooks)

        - At service start the diskcache locks are purged best-effort: a
            previous worker generation may have left stale acquires behind
            that would block actors after restart. A restart by --watch
            starts the service again and purges as well
        - With --no-purge the locks are kept, e.g. when further instances
            run on the same diskcache and still hold their locks

        - The worker processes start by forkserver on all python versions,
            so they never inherit the state of this process. Where
            forkserver is not available (e.g. Windows) spawn is used
        - With ```use_spawn``` or --use-spawn each worker process starts a
            fresh interpreter instead; --use-forkserver returns to
            forkserver when the config sets ```use_spawn```. The last of
            both switches wins

        """
        self.app.log.info('Spin up the dramatiq service workers')
        # resolve the worker options first, so an invalid config value
        # fails before sys.argv gets changed
        processes = self._worker_option('processes', 'worker_processes', allow_auto=True)
        threads = self._worker_option('threads', 'worker_threads')
        shutdown_timeout = self._worker_option('worker_shutdown_timeout', 'worker_shutdown_timeout')
        queue_prefetch = self._worker_option('queue_prefetch', 'queue_prefetch', allow_auto=True, config_min_value=0)
        delay_queue_prefetch = self._worker_option('delay_queue_prefetch', 'delay_queue_prefetch', allow_auto=True, config_min_value=0)
        worker_poll_interval = _number(
            'dramatiq config worker_poll_interval', self.app.dramatiq.config('worker_poll_interval'), allow_none=True
        )
        if worker_poll_interval is None and 'dramatiq_worker_timeout' in os.environ:
            # dramatiq takes the env then, check it before the workers start
            _number('env dramatiq_worker_timeout', os.environ['dramatiq_worker_timeout'])
        use_spawn = self.app.pargs.use_spawn
        if use_spawn is None:
            use_spawn = self.app.dramatiq.config('use_spawn')
            if not isinstance(use_spawn, bool):
                raise TokeoDramatiqError(f'Invalid dramatiq config use_spawn: Value {use_spawn!r} must be true or false!')
        # build a synthetic sys.argv that dramatiq's own argparser will
        # consume: cli.make_argument_parser().parse_args() reads from
        # sys.argv directly, so we override it here, then restore the
        # caller's argv afterwards so internal dramatiq restarts (which
        # re-exec the python process) see the original command line
        sys.argv = [sys.argv[0]]
        # append some worker settings; without --processes dramatiq takes
        # its default, the cpus of the host
        if processes != 'auto':
            sys.argv.extend(
                ['--processes', str(processes)],
            )
        sys.argv.extend(
            ['--threads', str(threads)],
        )
        sys.argv.extend(
            ['--worker-shutdown-timeout', str(shutdown_timeout)],
        )
        # restart_delay and prefetch are env-only on dramatiq's side, no
        # cli flags exist, so pass them via the environment; there 0 means
        # to follow the threads
        restart_delay = int(self.app.dramatiq.config('restart_delay'))
        queue_prefetch = 0 if queue_prefetch == 'auto' else queue_prefetch
        delay_queue_prefetch = 0 if delay_queue_prefetch == 'auto' else delay_queue_prefetch
        os.environ['dramatiq_restart_delay'] = str(restart_delay)
        os.environ['dramatiq_queue_prefetch'] = str(queue_prefetch)
        os.environ['dramatiq_delay_queue_prefetch'] = str(delay_queue_prefetch)
        # the worker binds this env as default argument on import, so only
        # the env reaches the freshly started workers, no constant does
        if worker_poll_interval is not None:
            os.environ['dramatiq_worker_timeout'] = str(worker_poll_interval)
        # dramatiq.worker reads the env only once on import into constants;
        # forked workers (default start method before python 3.14) inherit
        # them, so the already imported constants are updated as well
        dramatiq_worker.CONSUMER_RESTART_DELAY = restart_delay
        dramatiq_worker.CONSUMER_RESTART_DELAY_SECS = restart_delay / 1000
        dramatiq_worker.QUEUE_PREFETCH = queue_prefetch
        dramatiq_worker.DELAY_QUEUE_PREFETCH = delay_queue_prefetch
        # check for logging parameter
        if self.app.pargs.skip_logging:
            # add logging parameter
            sys.argv.extend(
                ['--skip-logging'],
            )
        elif self.app.debug:
            # dramatiq knows only info and debug, a single --verbose is enough
            sys.argv.extend(
                ['--verbose'],
            )
        # check for watch parameter
        if self.app.pargs.watch is not None:
            # add watcher for the module path of tasks
            sys.argv.extend(
                ['--watch', dirname(abspath(self.app.pargs.watch))],
            )
        # add the broker and actors
        sys.argv.extend(
            [
                self.app.dramatiq.config('serve'),
                self.app.dramatiq.config('actors'),
            ],
        )
        # --queues takes any number of values in dramatiq, so it must come
        # after the broker and actors it would swallow otherwise
        if self.app.pargs.queues:
            sys.argv.extend(
                ['--queues', *self.app.pargs.queues],
            )
        # parse the synthetic argv we just assembled
        args = cli.make_argument_parser().parse_args()
        # restore sys.argv: dramatiq forks workers and may re-exec on
        # restart, those processes pick the current sys.argv up again
        sys.argv = [sys.argv[0]] + self.app.argv
        # clear stale locks from a previously crashed worker generation;
        # best-effort since the diskcache extension may not be loaded; the
        # restored argv keeps --no-purge for the restarts by --watch too
        if not self.app.pargs.no_purge:
            try:
                self.app.dramatiq.locks.purge()
            except Exception:
                pass
        # tokeo sets the start method itself, python before 3.14 would fork
        # the workers with the state of this process; --use-spawn is never
        # handed to dramatiq, its set_start_method without force would fail
        start_method = 'spawn' if use_spawn else 'forkserver'
        if start_method not in multiprocessing.get_all_start_methods():
            self.app.log.debug(f'dramatiq start method {start_method} is not available, use spawn')
            start_method = 'spawn'
        multiprocessing.set_start_method(start_method, force=True)
        # signal hook
        for res in self.app.hook.run('tokeo_dramatiq_pre_start', self.app):
            pass
        # go and run dramatiq workers with the parsed args (blocks)
        result = cli.main(args)
        # signal hook
        for res in self.app.hook.run('tokeo_dramatiq_post_end', self.app):
            pass
        # signal result as exit code
        self.app.exit_code = result
