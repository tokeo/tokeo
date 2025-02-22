"""
Dramatiq Controller Module
==========================

This module integrates the Dramatiq message processing library with
a Cement CLI application, providing command-line interfaces to manage
Dramatiq workers for asynchronous task processing in a distributed system.

Features
--------
- Customizable worker settings through command-line arguments.
- Support for dynamically reloading actors and restarting workers on changes.
- Integration with the Cement framework for a consistent CLI experience.

Classes
-------
Dramatiq
    A Cement controller for managing Dramatiq service workers.
"""

import os
import sys
from os.path import basename, dirname, abspath
from tokeo.ext.argparse import Controller
from cement.core.meta import MetaMixin
from cement import ex
import dramatiq
from dramatiq import middleware, cli
from dramatiq.brokers.rabbitmq import RabbitmqBroker


class ExtendedRabbitmqBrocker(RabbitmqBroker):

    # from https://groups.io/g/dramatiq-users/topic/77913723

    def _build_queue_arguments(self, queue_name):
        arguments = super()._build_queue_arguments(queue_name)

        if queue_name.strip().lower().find('_unparalleled') >= 0:
            arguments['x-single-active-consumer'] = True

        return arguments


class TokeoDramatiq(MetaMixin):

    class Meta:
        """Extension meta-data."""

        #: Unique identifier for this handler
        label = 'tokeo.dramatiq'

        #: Id for config
        config_section = 'dramatiq'

        #: Dict with initial settings
        config_defaults = dict(
            serve='main:method',
            actors='actors.module',
            worker_threads=1,
            worker_processes=2,
            worker_shutdown_timeout=600_000,
            restart_delay=3_000,
            queue_prefetch=0,
            delay_queue_prefetch=0,
            broker='rabbitmq',
            rabbitmq_url='amqp://guest:guest@localhost:5672/',
            locks_tag='dramatiq_locks',
            locks_key_prefix='dq_',
        )

    def _setup(self, app):
        # save pointer to app
        self.app = app
        # prepare the config
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        # initialize locks handler
        self._locks = None
        # create a key/value store for locks if diskcache enabled
        if getattr(self.app._meta, 'cache_handler', None):
            try:
                self._locks = self.app.cache.locks_handler(
                    self._config('locks_tag'),
                    self._config('locks_key_prefix'),
                )
            except Exception:
                pass
        # dramatiq register
        self.register()

    def _config(self, key, **kwargs):
        """
        This is a simple wrapper, and is equivalent to:
            ``self.app.config.get(<section>, <key>)``.
        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    # create an alias for _config to access in controller
    config = _config

    def register(self):
        self.app.log.debug('Registering dramatiq middlewares and ExtendedRabbitmqBrocker ...')
        # re-build set of middlewares to use
        use_middleware = [
            m()
            for m in [
                middleware.AgeLimit,
                middleware.TimeLimit,
                middleware.ShutdownNotifications,
                middleware.Callbacks,
                middleware.Pipelines,
                middleware.Retries,
                middleware.CurrentMessage,
            ]
        ]
        # create the broker to RabbitMQ based on config
        rabbitmq_broker = ExtendedRabbitmqBrocker(
            url=self._config('rabbitmq_url'),
            middleware=use_middleware,
        )
        # globally set the broker to dramtiq
        dramatiq.set_broker(rabbitmq_broker)
        self.app.log.debug('ExtendedRabbitmqBrocker is registered as dramatiq broker')

    def close(self):
        self.app.log.debug('Closing dramatiq registered broker ...')
        # get the current registered broker
        broker = dramatiq.get_broker()
        # check that is equal to the registered one
        if isinstance(broker, ExtendedRabbitmqBrocker):
            # shutdown the connections
            broker.close()
            self.app.log.debug('Closed registered ExtendedRabbitmqBrocker')
        else:
            self.app.log.debug('Dramatiq broker instance is not a ExtendedRabbitmqBrocker')

    @property
    def locks(self):
        if self._locks:
            return self._locks
        else:
            raise AttributeError('Enable tokeo diskcache extension to allow locks')


class TokeoDramatiqController(Controller):
    """
    A Cement controller for managing Dramatiq service workers.

    This controller extends Cement's ``Controller`` class, offering
    functionalities specific to handling Dramatiq workers, including
    starting workers with configurable settings and reloading actors
    on file changes.

    Attributes
    ----------
    Meta : class
        Meta configuration class for the Cement controller.

    Methods
    -------
    serve()
        Starts the Dramatiq workers with optional settings for
        logging and file watching.

    """

    class Meta:
        """
        Meta configuration for the Dramatiq controller.

        Defines the label, type, parent, parser options, help text,
        description, and epilog for the controller.

        """

        label = 'dramatiq'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'manage the dramatiq service'
        description = 'Provides command-line interfaces to manage Dramatiq workers, enabling task processing in a distributed system.'
        epilog = f'Example: {basename(sys.argv[0])} dramatiq serve --skip-logging'

    def _setup(self, app):
        super(TokeoDramatiqController, self)._setup(app)

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
        ],
    )
    def serve(self):
        """
        Starts the Dramatiq workers with customizable settings.

        Parses command-line arguments to configure directly Dramatiq workers,
        including process and thread counts, logging, and file watching.

        """
        self.app.log.info('Spin up the dramatiq service workers')
        # prepare a sys.argv array to contorl the dramatiq main instance
        # initialize with "this" script (should by the running app)
        sys.argv = [sys.argv[0]]
        # append some worker settings
        sys.argv.extend(
            ['--processes', str(self.app.dramatiq.config('worker_processes'))],
        )
        sys.argv.extend(
            ['--threads', str(self.app.dramatiq.config('worker_threads'))],
        )
        sys.argv.extend(
            ['--worker-shutdown-timeout', str(self.app.dramatiq.config('worker_shutdown_timeout'))],
        )
        # some features ar set by environ
        os.environ['dramatiq_restart_delay'] = str(self.app.dramatiq.config('restart_delay'))
        os.environ['dramatiq_queue_prefetch'] = str(self.app.dramatiq.config('queue_prefetch'))
        os.environ['dramatiq_delay_queue_prefetch'] = str(self.app.dramatiq.config('delay_queue_prefetch'))
        # check for logging parameter
        if self.app.pargs.skip_logging:
            # add logging parameter
            sys.argv.extend(
                ['--skip-logging'],
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
        # parse sys.argv as dramatiq command line options
        args = cli.make_argument_parser().parse_args()
        # restore sys.argv for later restart etc. from inside dramatiq
        sys.argv = [sys.argv[0]] + self.app.argv
        # initialize locks on service start but ignore if missing
        try:
            self.app.dramatiq.locks.purge()
        except Exception:
            pass
        # signal hook
        for res in self.app.hook.run('tokeo_dramatiq_pre_start', self.app):
            pass
        # go and run dramatiq workers with the parsed args
        result = cli.main(args)
        # signal hook
        for res in self.app.hook.run('tokeo_dramatiq_post_end', self.app):
            pass
        # signal result as exit code
        self.app.exit_code = result
        return


def tokeo_dramatiq_extend_app(app):
    app.extend('dramatiq', TokeoDramatiq(app))
    app.dramatiq._setup(app)


def tokeo_dramatiq_shutdown(app):
    app.dramatiq.close()


def load(app):
    app.hook.define('tokeo_dramatiq_pre_start')
    app.hook.define('tokeo_dramatiq_post_end')
    app.handler.register(TokeoDramatiqController)
    app.hook.register('post_setup', tokeo_dramatiq_extend_app)
    app.hook.register('pre_close', tokeo_dramatiq_shutdown)
