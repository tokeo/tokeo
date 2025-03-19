"""
Dramatiq integration for asynchronous task processing in Tokeo applications.

This module provides integration between the Dramatiq message processing library
and Tokeo/Cement applications. It offers a complete solution for distributing
and processing asynchronous tasks across multiple workers using a message broker.

### Features:

- **RabbitMQ broker integration** with extended functionality
- **Distributed task processing** with configurable worker settings
- **Dynamic actor reloading** for development workflows
- **Command-line interface** for worker management
- **Single-active-consumer queues** through '_unparalleled' naming convention
- **Integration with diskcache** for distributed locks and rate limiting

### Example:

```python
from tokeo.ext.appshare import app

# Register an actor
@app.dramatiq.actor(queue_name="default")
def process_data(data_id):
    # This will be executed by workers asynchronously
    results = perform_complex_calculations(data_id)
    store_results(results)

# Enqueue a task
process_data.send(42)

# For rate-limited actors, use the locks
@app.dramatiq.actor(queue_name="api_calls")
@app.dramatiq.locks.throttle(count=10, per_seconds=60)
def call_external_api(resource_id):
    # Limited to 10 calls per minute across all workers
    return make_api_request(resource_id)
```

"""

import os
import sys
from os.path import basename, dirname, abspath
from cement.core.meta import MetaMixin
from cement import ex
import dramatiq
from dramatiq import middleware, cli, actor as dramatiq_actor
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from tokeo.ext.argparse import Controller


class ExtendedRabbitmqBrocker(RabbitmqBroker):
    """
    Extended RabbitMQ broker with support for single-active-consumer queues.

    This class extends the standard Dramatiq RabbitmqBroker to add support for
    the RabbitMQ single-active-consumer feature. This feature ensures that only
    one consumer processes messages from a queue at a time, which is useful for
    tasks that require strict ordering or exclusive access to resources.

    ### Notes:

    : To use this feature, simply include '_unparalleled' in your queue name.
      For example: 'my_ordered_tasks_unparalleled'

    : Implementation based on solution from the Dramatiq users group:
      https://groups.io/g/dramatiq-users/topic/77913723
    """

    def _build_queue_arguments(self, queue_name):
        """
        Build queue arguments with single-active-consumer support.

        Extends the standard queue arguments by adding the x-single-active-consumer
        flag for queues with '_unparalleled' in their name.

        ### Args:

        - **queue_name** (str): The name of the queue being created or declared

        ### Returns:

        - **dict**: Queue arguments for RabbitMQ

        """
        arguments = super()._build_queue_arguments(queue_name)

        if queue_name.strip().lower().find('_unparalleled') >= 0:
            arguments['x-single-active-consumer'] = True

        return arguments


class TokeoDramatiq(MetaMixin):
    """
    Main Dramatiq integration for Tokeo applications.

    This class provides the core Dramatiq functionality for Tokeo, including
    broker configuration, middleware setup, connection management, and
    integration with diskcache for distributed locking and rate limiting.

    The class is instantiated and attached to the app as 'app.dramatiq' during
    application startup.

    ### Notes:

    - All configuration is read from the 'dramatiq' section in the application config
    - The diskcache extension is required for the locks and rate limiting functionality
    - Only RabbitMQ broker is currently supported

    """

    class Meta:
        """Extension meta-data and configuration defaults."""

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
        # safe references to dramtiq lib
        self.actor = dramatiq_actor
        # dramatiq register
        self.register()

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a convenient wrapper around the application's config.get method,
        accessing values from the extension's config section.

        ### Args:

        - **key** (str): Configuration key to retrieve
        - **kwargs**: Additional arguments passed to config.get()

        ### Returns:

        - **Any**: The configuration value for the specified key

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

        ### Notes:

        : Configures a standard set of middleware components including AgeLimit,
          TimeLimit, ShutdownNotifications, Callbacks, Pipelines, Retries, and
          CurrentMessage

        : Creates an ExtendedRabbitmqBrocker instance with the configured URL
          and registers it as the global Dramatiq broker

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

        ### Notes:

        - Only closes the broker if it's an instance of ExtendedRabbitmqBrocker
        - Logs diagnostic messages about the broker closure process

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

        ### Returns:

        - **TokeoDiskCacheLocksHandler**: The locks handler for rate limiting

        ### Raises:

        - **AttributeError**: If the diskcache extension is not enabled

        ### Example:

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

    This controller extends Cement's Controller class, offering
    functionalities specific to handling Dramatiq workers, including
    starting workers with configurable settings and reloading actors
    on file changes.

    ### Notes:

    - Provides CLI commands for managing Dramatiq workers and locks

    - Command-line arguments enable customizing worker behavior at runtime
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
        """
        Manage distributed locks used by Dramatiq tasks.

        This command provides access to the distributed locks stored in the diskcache.
        It can display information about the locks configuration or purge all locks
        from the cache when needed.

        ### Notes:

        - The `--purge` flag removes all locks from the cache, which can be useful
          when locks need to be reset after system failures

        - Without arguments, displays information about the lock configuration

        ### Output:

        : Prints lock configuration information or confirmation of purge operation

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
        ],
    )
    def serve(self):
        """
        Start Dramatiq workers with customizable settings.

        This command starts Dramatiq worker processes that process tasks from the
        message queues. It applies configuration from the application config while
        allowing CLI arguments to override specific settings.

        ### Args:

        - **--skip-logging** (flag): Skip Dramatiq's default logging configuration
        - **--watch** (str): Path to watch for file changes to reload actors

        ### Notes:

        - Worker configuration includes process count, thread count, shutdown timeout,
          restart delay, and queue prefetch settings

        - When using --watch, the command will monitor the specified directory for
          changes and automatically reload actors when files change

        - The command configures Dramatiq CLI arguments and environment variables
          before delegating to Dramatiq's main function

        ### Output:

        1. Worker processes log their activity to the console
        1. The command blocks until workers are terminated

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


def tokeo_dramatiq_pdoc_pre_render(app):
    """
    Replace the dramatiq decorator with a simple one for pdoc rendering.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    : This function replaces the complex Dramatiq actor decorator
      with a simpler version that pdoc can handle

    """
    from tokeo.core.utils.pdoc import pdoc_replace_decorator

    dramatiq.actor = pdoc_replace_decorator
    if hasattr(app.dramatiq, 'actor'):
        app.dramatiq.actor = pdoc_replace_decorator


def tokeo_dramatiq_pdoc_post_render(app):
    """
    Restore the original dramatiq decorator after pdoc rendering.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    : This function restores the original Dramatiq actor decorator functionality
    """
    dramatiq.actor = dramatiq_actor
    if hasattr(app.dramatiq, 'actor'):
        app.dramatiq.actor = dramatiq_actor


def tokeo_dramatiq_pdoc_render_decorator(app, func, decorator, args, kwargs):
    """
    Handle docstrings for dramatiq decorators in pdoc.

    This function provides custom handling for Dramatiq actor decorators
    during pdoc documentation rendering.

    ### Args:

    - **app** (Application): The Cement application instance
    - **func** (function): The function being decorated
    - **decorator** (str): The decorator string
    - **args** (list): Positional arguments to the decorator
    - **kwargs** (dict): Keyword arguments to the decorator

    ### Returns:

    - **dict|None**: Dictionary with decorator information or None if not handled

    ### Notes:

    - This function extracts queue_name parameters from decorators to
      provide meaningful documentation

    - It works with both the global dramatiq.actor and app.dramatiq.actor forms

    """
    if decorator == '@dramatiq.actor' or decorator == '@app.dramatiq.actor':
        params = None
        if kwargs is not None and 'queue_name' in kwargs:
            try:
                value = kwargs['queue_name'].value
                params = f'queue_name="{value}"' if isinstance(value, str) else f'queue_name={value}'
            except Exception:
                params = 'queue_name=...'
        return dict(
            decorator=decorator,
            params=params,
            docstring=app.pdoc.docstrings('decorator', 'dramatiq.actor'),
        )


def tokeo_dramatiq_extend_app(app):
    """
    Initialize and register the Dramatiq extension with the application.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    - This function is called during application setup

    - It creates the TokeoDramatiq instance and attaches it to the app
      as app.dramatiq

    """
    app.extend('dramatiq', TokeoDramatiq(app))
    app.dramatiq._setup(app)


def tokeo_dramatiq_shutdown(app):
    """
    Perform cleanup when shutting down the Dramatiq extension.

    This function closes broker connections and performs other cleanup tasks
    when the application is shutting down.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    - Called during application shutdown to properly close connections

    - Important for clean application termination without resource leaks

    """
    app.dramatiq.close()


def load(app):
    """
    Load the Dramatiq extension into the application.

    This function is called by Cement when loading extensions. It defines hooks
    for application integration, registers the controller for command-line access,
    and sets up initialization and shutdown hooks.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    1. Registers the TokeoDramatiqController for CLI commands

    1. Defines extension-specific hooks that can be used by other extensions

    1. Sets up integration with pdoc for documentation generation

    """
    # Define hooks that can be used by other extensions
    app.hook.define('tokeo_dramatiq_pre_start')
    app.hook.define('tokeo_dramatiq_post_end')
    # Register the command-line controller
    app.handler.register(TokeoDramatiqController)
    # Register initialization and shutdown hooks
    app.hook.register('post_setup', tokeo_dramatiq_extend_app)
    app.hook.register('pre_close', tokeo_dramatiq_shutdown)
    # register for pdoc
    app.hook.register('tokeo_pdoc_pre_render', tokeo_dramatiq_pdoc_pre_render)
    app.hook.register('tokeo_pdoc_post_render', tokeo_dramatiq_pdoc_post_render)
    app.hook.register('tokeo_pdoc_render_decorator', tokeo_dramatiq_pdoc_render_decorator)
