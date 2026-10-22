"""
Tokeo Dramatiq extension controller.

The ```dramatiq``` commands of the app: ```serve``` starts the worker
processes from the config and its switches, ```locks``` shows or purges the
diskcache locks of the actors, ```queues``` shows and maintains the queues.

"""

import os
import sys
import multiprocessing
from os.path import basename, dirname, abspath
from cement import ex
from tokeo.core.utils.json import json_dump
from dramatiq import cli
from tokeo.ext.argparse import Controller
from tokeo.ext.dramatiq.base import _number
from tokeo.ext.dramatiq.exc import TokeoDramatiqError, TokeoDramatiqConfigError


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

        - **TokeoDramatiqConfigError**: If the switch or the config value is
            not allowed

        """
        # a given switch replaces the config value
        value = getattr(self.app.pargs, option)
        if value is not None:
            return _number('switch', f'--{option.replace("_", "-")}', value, allow_auto=allow_auto)
        value = _number('config', key, self.app.dramatiq.config(key), min_value=config_min_value, allow_auto=allow_auto)
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
        help='show and maintain the dramatiq queues',
        description='Show the state of the dramatiq queues of the app and maintain them.',
        epilog=f'Use "{basename(sys.argv[0])} dramatiq queues" to show the state of all queues of the app.',
        arguments=[
            (['names'], dict(nargs='*', metavar='QUEUE', help='queues to handle, all queues of the app without')),
            (['--json'], dict(action='store_true', help='print the result as json')),
            (['--dead'], dict(nargs='?', const='10', metavar='N', help='show the first N dead letters (default 10)')),
            (['--purge'], dict(action='store_true', help='remove the messages and keep the queues')),
            (['--dead-only'], dict(action='store_true', help='purge only the dead letters')),
            (['--delete'], dict(action='store_true', help='delete the queues')),
            (['--recreate'], dict(action='store_true', help='delete the queues and declare them again')),
            (['--requeue-dead'], dict(action='store_true', help='move the dead letters back into their queue')),
            (['--limit'], dict(metavar='N', help='dead letters to move by --requeue-dead, all without')),
            (['--yes'], dict(action='store_true', help='confirm a change of queues or messages')),
            (['--force'], dict(action='store_true', help='delete or recreate also queues with messages or consumers')),
        ],
    )
    def queues(self):
        """
        Show the state of the dramatiq queues and maintain them.

        ### Raises

        - **TokeoDramatiqError**: If the switches do not fit together, a
            change misses --yes, or a queue refuses the change

        ### Notes

        - Without queue names all queues of the actors of the app are used
        - A change of queues or messages needs --yes

        """
        pargs = self.app.pargs
        queues = self.app.dramatiq.queues
        names = pargs.names or None
        actions = [action for action in ('purge', 'delete', 'recreate', 'requeue_dead') if getattr(pargs, action)]
        if len(actions) > 1:
            raise TokeoDramatiqError(f'Use only one of {", ".join(_switch(a) for a in actions)}!')
        if pargs.dead_only and actions != ['purge']:
            raise TokeoDramatiqError('Use --dead-only together with --purge!')
        if actions and not pargs.yes:
            raise TokeoDramatiqError(f'{_switch(actions[0])} changes queues or messages, confirm it by --yes!')
        if (pargs.requeue_dead or pargs.dead is not None) and not names:
            raise TokeoDramatiqError('Name the queues for --dead or --requeue-dead!')
        if actions == ['purge']:
            result = queues.purge(names, dead_only=pargs.dead_only)
            lines = [f'{r["name"]}: purged {r["purged"]} messages' for r in result]
        elif actions == ['delete']:
            result = queues.delete(names, force=pargs.force)
            lines = [f'{r["name"]}: {"deleted" if r["deleted"] else "not found"}' for r in result]
        elif actions == ['recreate']:
            result = queues.recreate(names, force=pargs.force)
            lines = [f'{r["name"]}: recreated with {r["arguments"]}' for r in result]
        elif actions == ['requeue_dead']:
            limit = _number('switch', '--limit', pargs.limit, allow_none=True)
            result = [queues.requeue_dead(name, limit) for name in names]
            lines = [f'{r["name"]}: requeued {r["requeued"]} dead letters' for r in result]
        else:
            result = queues.state(names)
            if pargs.dead is not None:
                limit = _number('switch', '--dead', pargs.dead)
                for state in result:
                    state['dead_letters'] = queues.peek_dead(state['name'], limit)
            lines = _state_lines(result)
        self.app.print(json_dump(result, indent=2) if pargs.json else '\n'.join(lines))

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
        - **--no-purge** (flag): Keep the diskcache locks
        - **-p, --processes** (int|auto): Number of worker processes
        - **-t, --threads** (int): Number of worker threads per process
        - **--queue-prefetch** (int|auto): Messages to prefetch per queue
        - **--delay-queue-prefetch** (int|auto): Prefetch per delay queue
        - **--worker-shutdown-timeout** (int): Shutdown timeout in milliseconds
        - **--use-spawn** (flag): Start the worker processes by spawn
        - **--use-forkserver** (flag): Start the worker processes by forkserver
        - **--queues** (str): Queues to consume, all queues without it

        ### Raises

        - **TokeoDramatiqConfigError**: If a switch, a config value or a
            dramatiq env is not allowed

        ### Notes

        - Worker configuration includes process count, thread count, shutdown
            timeout, restart delay, and queue prefetch settings
        - Each worker switch replaces its config value
        - With --watch, the command monitors the given directory for changes
            and reloads actors when files change
        - In debug mode dramatiq logs on debug level, unless --skip-logging

        - Worker processes log to the console; the call blocks until they
            terminate (signal, error or shutdown via the management hooks)

        - At service start the diskcache locks are purged best-effort: a
            previous worker generation may have left stale acquires behind
            that would block actors after restart

        - The worker processes start by forkserver, or by spawn if set or
            if forkserver is not available

        """
        self.app.log.info('Spin up the dramatiq service workers')
        # check all values before sys.argv gets changed
        processes = self._worker_option('processes', 'worker_processes', allow_auto=True)
        threads = self._worker_option('threads', 'worker_threads')
        shutdown_timeout = self._worker_option('worker_shutdown_timeout', 'worker_shutdown_timeout')
        queue_prefetch = self._worker_option('queue_prefetch', 'queue_prefetch', allow_auto=True, config_min_value=0)
        delay_queue_prefetch = self._worker_option('delay_queue_prefetch', 'delay_queue_prefetch', allow_auto=True, config_min_value=0)
        worker_poll_interval = _number('config', 'worker_poll_interval', self.app.dramatiq.config('worker_poll_interval'), allow_none=True)
        if worker_poll_interval is None and 'dramatiq_worker_timeout' in os.environ:
            # dramatiq takes the env then, check it before the workers start
            _number('env', 'dramatiq_worker_timeout', os.environ['dramatiq_worker_timeout'])
        use_spawn = self.app.pargs.use_spawn
        if use_spawn is None:
            use_spawn = self.app.dramatiq.config('use_spawn')
            if not isinstance(use_spawn, bool):
                raise TokeoDramatiqConfigError('config', 'use_spawn', use_spawn, 'true or false')
        # build a synthetic sys.argv that dramatiq's own argparser will
        # consume: cli.make_argument_parser().parse_args() reads from
        # sys.argv directly, so we override it here, then restore the
        # caller's argv afterwards so internal dramatiq restarts (which
        # re-exec the python process) see the original command line
        sys.argv = [sys.argv[0]]
        # append some worker settings, without --processes dramatiq takes the cpus
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
        # cli flags exist, so pass them via the environment
        restart_delay = int(self.app.dramatiq.config('restart_delay'))
        queue_prefetch = 0 if queue_prefetch == 'auto' else queue_prefetch
        delay_queue_prefetch = 0 if delay_queue_prefetch == 'auto' else delay_queue_prefetch
        os.environ['dramatiq_restart_delay'] = str(restart_delay)
        os.environ['dramatiq_queue_prefetch'] = str(queue_prefetch)
        os.environ['dramatiq_delay_queue_prefetch'] = str(delay_queue_prefetch)
        # only the env reaches the workers, they bind the value on import
        if worker_poll_interval is not None:
            os.environ['dramatiq_worker_timeout'] = str(worker_poll_interval)
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
        # --queues takes any number of values, so it follows broker and actors
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
        # best-effort since the diskcache extension may not be loaded
        if not self.app.pargs.no_purge:
            try:
                self.app.dramatiq.locks.purge()
            except Exception:
                pass
        # set the start method here, dramatiq would fail on it without force
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


def _switch(action):
    """Name the switch of a queues action."""
    return '--' + action.replace('_', '-')


def _state_lines(states):
    """Format the state of queues as table, followed by their dead letters."""
    rows = [('QUEUE', 'READY', 'DELAYED', 'DEAD', 'CONSUMERS', 'FLAGS')]
    for state in states:
        flags = [
            flag
            for flag, set_ in (
                ('missing', not state['exists']),
                ('unparalleled', state['unparalleled']),
                ('no consumer', state['exists'] and not state['consumers']),
                ('no actor', not state['actors']),
            )
            if set_
        ]
        counts = ['-' if state[key] is None else str(state[key]) for key in ('ready', 'delayed', 'dead', 'consumers')]
        rows.append((state['name'], *counts, ', '.join(flags)))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = [
        '  '.join(cell.ljust(width) if i in (0, 5) else cell.rjust(width) for i, (cell, width) in enumerate(zip(row, widths))).rstrip()
        for row in rows
    ]
    for state in states:
        if state.get('dead_letters'):
            lines.append(f'\ndead letters of {state["name"]}:')
            for letter in state['dead_letters']:
                lines.append(f'  {letter["message_id"]}  {letter["actor"]}  retries={letter["retries"]}  {letter["error"] or ""}'.rstrip())
                lines.append(f'    args: {letter["args"]}')
    return lines
