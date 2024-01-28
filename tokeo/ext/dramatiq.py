"""
Dramatiq Controller Module
==========================

This module integrates the Dramatiq message processing library with a Cement CLI application,
providing command-line interfaces to manage Dramatiq workers for asynchronous task processing
in a distributed system.

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

import sys
from os.path import basename, dirname, abspath
from cement.core.meta import MetaMixin
from cement import Controller, ex
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
            broker='rabbitmq',
            rabbitmq_url='amqp://guest:guest@localhost:5672/',
        )

    def __init__(self, app, *args, **kw):
        super(TokeoDramatiq, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        self.register()

    def _config(self, key, default=None):
        """
        This is a simple wrapper, and is equivalent to: ``self.app.config.get(<section>, <key>)``.
        """
        return self.app.config.get(self._meta.config_section, key)

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


class TokeoDramatiqController(Controller):
    """
    A Cement controller for managing Dramatiq service workers.

    This controller extends Cement's ``Controller`` class, offering functionalities specific
    to handling Dramatiq workers, including starting workers with configurable settings and
    reloading actors on file changes.

    Attributes
    ----------
    Meta : class
        Meta configuration class for the Cement controller.

    Methods
    -------
    serve()
        Starts the Dramatiq workers with optional settings for logging and file watching.

    """

    class Meta:
        """
        Meta configuration for the Dramatiq controller.

        Defines the label, type, parent, parser options, help text, description, and
        epilog for the controller.

        """

        label = 'dramatiq'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'Manage the dramatiq service'
        description = 'Provides command-line interfaces to manage Dramatiq workers, enabling task processing in a distributed system.'
        epilog = f'Example: {basename(sys.argv[0])} dramatiq serve --skip-logging\n '

    def _setup(self, app):
        super(TokeoDramatiqController, self)._setup(app)

    def _default(self):
        self._parser.print_help()

    @ex(
        help='Spin up the dramatiq service workers',
        description='Starts Dramatiq workers, allowing for configuration of worker processes and threads. Optional flags for logging and file watching.',
        epilog=f'Use "{basename(sys.argv[0])} dramatiq serve" with options like "--skip-logging" for custom logging or "--watch" for automatic actor reloading.',
        arguments=[
            (
                ['--skip-logging'],
                dict(
                    action='store_true',
                    help='Do not call dramatiq logging.basicConfig()',
                ),
            ),
            (
                ['--watch'],
                dict(
                    action='store',
                    help='Reload actors on changes and restart workers',
                ),
            ),
        ],
    )
    def serve(self):
        """
        Starts the Dramatiq workers with customizable settings.

        Parses command-line arguments to configure the behavior of Dramatiq workers,
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
        # restore the sys.argv content for later restart etc. from inside dramatiq
        sys.argv = [sys.argv[0]] + self.app.argv
        # go and run dramatiq workers with the parsed args
        sys.exit(cli.main(args))


def tokeo_dramatiq_extend_app(app):
    app.extend('dramatiq', TokeoDramatiq(app))
    app.dramatiq._setup(app)


def tokeo_dramatiq_shutdown(app):
    app.dramatiq.close()


def load(app):
    app.handler.register(TokeoDramatiqController)
    app.hook.register('post_setup', tokeo_dramatiq_extend_app)
    app.hook.register('pre_close', tokeo_dramatiq_shutdown)
