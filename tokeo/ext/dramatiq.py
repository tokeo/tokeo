"""
Dramatiq integration for asynchronous task processing in Tokeo applications.

This module provides integration between the Dramatiq message processing library
and Tokeo/Cement applications. It offers a complete solution for distributing
and processing asynchronous tasks across multiple workers using a message broker.

Key features:
- RabbitMQ broker integration with extended functionality
- Distributed task processing with configurable worker settings
- Dynamic actor reloading for development workflows
- Command-line interface for worker management
- Support for single-active-consumer queues through '_unparalleled' naming
- Integration with diskcache for distributed locks and rate limiting

Example:
    ```python
    from tokeo.ext import dramatiq

    # Register an actor
    @dramatiq.actor(queue_name="default")
    def process_data(data_id):
        # This will be executed by workers asynchronously
        results = perform_complex_calculations(data_id)
        store_results(results)

    # Enqueue a task
    process_data.send(42)

    # For rate-limited actors, use the locks
    @dramatiq.actor(queue_name="api_calls")
    @app.dramatiq.locks.throttle(count=10, per_seconds=60)
    def call_external_api(resource_id):
        # Limited to 10 calls per minute across all workers
        return make_api_request(resource_id)
    ```

Command-line interface:
    `tokeo dramatiq serve [options]` - Start worker processes
    `tokeo dramatiq locks --purge` - Manage distributed locks
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
    """
    Extended RabbitMQ broker with support for single-active-consumer queues.

    This class extends the standard Dramatiq RabbitmqBroker to add support for
    the RabbitMQ single-active-consumer feature. This feature ensures that only
    one consumer processes messages from a queue at a time, which is useful for
    tasks that require strict ordering or exclusive access to resources.

    To use this feature, simply include '_unparalleled' in your queue name.
    For example: 'my_ordered_tasks_unparalleled'.

    Notes:
        Implementation based on solution from the Dramatiq users group:
        https://groups.io/g/dramatiq-users/topic/77913723
    """

    def _build_queue_arguments(self, queue_name):
        """
        Build queue arguments with single-active-consumer support.

        Extends the standard queue arguments by adding the x-single-active-consumer
        flag for queues with '_unparalleled' in their name.

        Args:
            queue_name: The name of the queue being created or declared

        Returns:
            dict: Queue arguments for RabbitMQ
        """
        arguments = super()._build_queue_arguments(queue_name)

        if queue_name.strip().lower().find('_unparalleled') >= 0:
            arguments['x-single-active-consumer'] = True

        return arguments


class TokeoDramatiq(MetaMixin):
    """
    Main Dramatiq integration for Tokeo applications.

    This class provides the core Dramatiq functionality for Tokeo, including:

    1. Configuration of Dramatiq brokers and middleware
    2. Integration with the diskcache extension for distributed locking
    3. Management of RabbitMQ connections and resources
    4. Access to locks for rate limiting and concurrency control

    The class is instantiated and attached to the app as 'app.dramatiq' during
    application startup.

    Attributes:
        app: The Cement application instance
        _locks: Optional locks handler from diskcache extension
    """

    class Meta:
        """
        Extension meta-data and configuration defaults."""

        #: Unique identifier for this handler
        label = 'tokeo.dramatiq'

        #: Id for config
        config_section = 'dramatiq'

        #: Dict with initial settings
        config_defaults = dict(
            # Entry point method for the workers
            serve='main:method',
            # Module(s) containing actor definitions
            actors='actors.module',
            # Number of worker threads per process
            worker_threads=1,
            # Number of worker processes to run
            worker_processes=2,
            # Worker shutdown timeout in milliseconds
            worker_shutdown_timeout=600_000,
            # Delay between worker restarts in milliseconds
            restart_delay=3_000,
            # Number of messages to prefetch from queues
            queue_prefetch=0,
            # Number of messages to prefetch from delay queues
            delay_queue_prefetch=0,
            # Broker type (currently only rabbitmq supported)
            broker='rabbitmq',
            # RabbitMQ connection URL
            rabbitmq_url='amqp://guest:guest@localhost:5672/',
            # Tag for dramatiq locks in the cache
            locks_tag='dramatiq_locks',
            # Prefix for dramatiq lock keys
            locks_key_prefix='dq_',
        )

    def _setup(self, app):
        """
        Initialize the Dramatiq extension.

        This method is called during application setup to initialize the Dramatiq
        extension, merge configuration settings, set up the locks handler if
        available, and register the broker and middleware.

        Args:
            app: The Cement application instance.
        """
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
        Access configuration from the dramatiq config section.

        This method is a simple wrapper around app.config.get that automatically
        uses the correct config section for Dramatiq settings.

        Args:
            key: Configuration key to retrieve
            **kwargs: Additional arguments passed to app.config.get

        Returns:
            The configuration value for the given key.
        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    # create an alias for _config to access in controller
    config = _config

    def register(self):
        """
        Register the Dramatiq broker and middleware.

        Sets up the Dramatiq middleware stack and creates the RabbitMQ broker
        instance with the configured settings. This method is called during
        initialization and configures Dramatiq for use within the application.
        """
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
        """
        Close the Dramatiq broker connection.

        Called during application shutdown to properly close the connection
        to the RabbitMQ broker, ensuring clean resource release.
        """
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
        """
        Access the distributed locks handler for rate limiting.

        This property provides access to the distributed locks handler from the
        diskcache extension, which can be used to apply rate limiting and
        concurrency control to Dramatiq actors.

        Returns:
            TokeoDiskCacheLocksHandler: The locks handler for rate limiting

        Raises:
            AttributeError: If the diskcache extension is not enabled

        Example:
            ```python
            @dramatiq.actor
            @app.dramatiq.locks.throttle(count=5, per_seconds=60)
            def rate_limited_task(arg):
                # Limited to 5 calls per minute across all workers
                pass
            ```
        """
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
    """
    Initialize and register the Dramatiq extension with the application.

    Args:
        app: The Cement application instance.
    """
    app.extend('dramatiq', TokeoDramatiq(app))
    app.dramatiq._setup(app)


def tokeo_dramatiq_shutdown(app):
    """
    Perform cleanup when shutting down the Dramatiq extension.

    Closes broker connections and performs other cleanup tasks.

    Args:
        app: The Cement application instance.
    """
    app.dramatiq.close()


def load(app):
    """
    Load the Dramatiq extension into the application.

    This function is called by Cement when loading extensions. It:

    1. Defines hooks for application integration
    2. Registers the controller for command-line access
    3. Sets up initialization and shutdown hooks

    Args:
        app: The Cement application instance.
    """
    # Define hooks that can be used by other extensions
    app.hook.define('tokeo_dramatiq_pre_start')
    app.hook.define('tokeo_dramatiq_post_end')
    # Register the command-line controller
    app.handler.register(TokeoDramatiqController)
    # Register initialization and shutdown hooks
    app.hook.register('post_setup', tokeo_dramatiq_extend_app)
    app.hook.register('pre_close', tokeo_dramatiq_shutdown)
