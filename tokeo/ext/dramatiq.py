"""
Tokeo Dramatiq Extension Module.

Dramatiq integration for asynchronous task processing in Tokeo applications.

Wraps Dramatiq behind a Tokeo extension: a RabbitMQ broker subclass with a
queue-naming convention for single-active-consumer queues, a Cement
controller that drives ```dramatiq``` workers from the application config, and
optional integration with the diskcache locks for cross-worker rate limiting.

### Features

- Single-active-consumer queues via the '_unparalleled' name suffix
    (RabbitMQ x-single-active-consumer flag set on declare)

- Distributed locks and rate limiting on actors when the diskcache
    extension is loaded (best-effort: missing without diskcache)

- ```dramatiq serve``` controller with worker process/thread counts and
    optional --watch path for actor hot-reload during development

- pdoc render hooks that swap the runtime decorator for a doc-friendly
    stub so generated docs show ```@dramatiq.actor``` signatures cleanly

### Example

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
import argparse
import multiprocessing
from os.path import basename, dirname, abspath
from cement.core.meta import MetaMixin
from cement import ex
import dramatiq
from dramatiq import middleware, cli, actor as dramatiq_actor
from dramatiq import worker as dramatiq_worker
from dramatiq.asyncio import EventLoopThread, set_event_loop_thread
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.brokers import rabbitmq as dramatiq_rabbitmq
import pika
from tokeo.core.exc import TokeoError
from tokeo.core.utils.url import overload_with_auth as url_overload_with_auth
from tokeo.core.utils.tls import create_ssl_context as tls_create_ssl_context
from tokeo.core.utils.base import as_int_or_auto
from tokeo.ext.argparse import Controller


class TokeoDramatiqError(TokeoError):
    """
    Exception class for Dramatiq-extension-related errors.

    Used for errors specific to this extension.

    """

    pass


class TokeoAsyncIO(middleware.AsyncIO):
    """
    AsyncIO middleware whose event loop thread does not block the exit.

    A worker process that gets a second SIGTERM ends by ```sys.exit()```
    without ```after_worker_shutdown```, so the event loop thread of the
    standard middleware keeps running and the process hangs forever. This
    happens whenever SIGTERM reaches the whole process group, e.g. by systemd
    with the default ```KillMode=control-group```.

    ### Notes

    : The normal shutdown is unchanged, ```after_worker_shutdown``` still
        stops and joins the thread

    : Remove this class once dramatiq starts the thread as daemon itself

    """

    def before_worker_boot(self, broker, worker):
        """Start the event loop thread as daemon for the worker."""
        event_loop_thread = EventLoopThread(self.logger)
        # a daemon thread lets a killed worker process end
        event_loop_thread.daemon = True
        event_loop_thread.start(timeout=1.0)
        set_event_loop_thread(event_loop_thread)


class ExtendedRabbitmqBroker(RabbitmqBroker):
    """
    Extended RabbitMQ broker with support for single-active-consumer queues.

    This class extends the standard Dramatiq RabbitmqBroker to add support for
    the RabbitMQ single-active-consumer feature. This feature ensures that only
    one consumer processes messages from a queue at a time, which is useful for
    tasks that require strict ordering or exclusive access to resources.

    ### Notes

    - To use this feature, simply include '_unparalleled' in your queue name,
        for example: 'my_ordered_tasks_unparalleled'
    - Implementation based on solution from the Dramatiq users group:
        https://groups.io/g/dramatiq-users/topic/77913723
    - Also accepts auth_identity/auth_password and merges them into the
        connection url, since RabbitmqBroker cannot take a url and separate
        pika credentials at the same time
    - For amqps urls it accepts tls_verify_hostname/tls_verify_cert/tls_ca to
        customize the ssl context; a custom context forces the pika
        parameters path, since RabbitmqBroker cannot mix url and parameters

    """

    def __init__(
        self, *, url=None, auth_identity=None, auth_password=None, tls_verify_hostname=True, tls_verify_cert=True, tls_ca=None, **kwargs
    ):
        """
        Initialize the broker, applying credentials and tls to the connection.

        Accepts the standard RabbitmqBroker keyword arguments plus the auth and
        tls options below. Credentials are merged into the url userinfo (see
        overload_with_auth). For an amqps url the tls options build an ssl
        context (see create_ssl_context); when one is needed the url is
        decomposed and handed to the parent as pika parameters, since
        RabbitmqBroker cannot take a url and pika parameters together.

        ### Args

        - **url** (str, optional): The amqp(s) connection url
        - **auth_identity** (str, optional): Username/identity to force into
            the url userinfo, overriding any user the url carries
        - **auth_password** (str, optional): Password to force into the url
            userinfo, overriding any password the url carries
        - **tls_verify_hostname** (bool|str): Verify the certificate hostname;
            a string pins the expected name, False skips the hostname check
        - **tls_verify_cert** (bool): Verify the certificate chain; False
            disables verification (CERT_NONE)
        - **tls_ca** (str, optional): Path to a CA bundle to trust instead of
            the system store

        ### Keyword Args

        - **kwargs**: Remaining RabbitmqBroker arguments (e.g. middleware,
            confirm_delivery, max_priority, parameters)

        """
        url = url_overload_with_auth(url, auth_identity, auth_password)
        # tls is driven by the amqps scheme; the tls_* options only customize
        # it, so a plain amqp url ignores them
        context = server_hostname = None
        if url and url.lower().startswith('amqps'):
            context, server_hostname = tls_create_ssl_context(
                tls_verify_hostname,
                tls_verify_cert,
                tls_ca,
            )
        if context is None:
            # plain amqp, or amqps with secure defaults where pika builds its
            # own context from the url
            super().__init__(url=url, **kwargs)
        else:
            # a custom ssl context cannot ride inside the url, and the parent
            # forbids url + parameters, so decompose the url and hand pika
            # ConnectionParameters carrying our ssl_options instead
            params = pika.URLParameters(url)
            super().__init__(
                parameters=[
                    dict(
                        host=params.host,
                        port=params.port,
                        virtual_host=params.virtual_host,
                        credentials=params.credentials,
                        ssl_options=pika.SSLOptions(context, server_hostname),
                    )
                ],
                **kwargs,
            )

    def _build_queue_arguments(self, queue_name):
        """
        Build queue arguments with single-active-consumer support.

        Extends the standard queue arguments by adding the x-single-active-consumer
        flag for queues with '_unparalleled' in their name.

        ### Args

        - **queue_name** (str): The name of the queue being created or declared

        ### Returns

        - **dict**: Queue arguments for RabbitMQ

        """
        arguments = super()._build_queue_arguments(queue_name)

        if '_unparalleled' in queue_name.lower():
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

    ### Notes

    - Configuration is read from the 'dramatiq' section in the application config
    - The diskcache extension is required for the locks and rate limiting
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
            # Number of worker processes to run; auto follows the dramatiq
            # defaults and counts the cpus of the host
            worker_processes=2,
            # Worker shutdown timeout in milliseconds
            worker_shutdown_timeout=600_000,
            # Delay between worker restarts in milliseconds
            restart_delay=3_000,
            # Milliseconds a worker thread waits for a message or a task
            # before it checks for due delayed messages and for shutdown;
            # dramatiq names it worker_timeout (env dramatiq_worker_timeout).
            # None keeps the value of dramatiq, from the env or else 1000; a
            # value here wins over the env
            worker_poll_interval=None,
            # Number of messages to prefetch from queues; auto (or 0) follows
            # the dramatiq defaults
            queue_prefetch='auto',
            # Number of messages to prefetch from delay queues; auto (or 0)
            # follows the dramatiq defaults
            delay_queue_prefetch='auto',
            # Start the worker processes by spawn instead of forkserver
            use_spawn=False,
            # Milliseconds an actor may run per message before it gets
            # interrupted; dramatiq knows no unlimited run, a long job sets
            # its own limit on the actor, e.g. time_limit=3_600_000
            time_limit=600_000,
            # Milliseconds after sending, a message gets skipped instead of
            # processed; None processes messages of any age
            max_age=None,
            # Interrupt running actors by a Shutdown exception when the
            # worker stops; single actors may opt in by notify_shutdown=True
            notify_shutdown=False,
            # Retries of a failed message before it goes to the dead letters
            max_retries=20,
            # Milliseconds of the first retry delay, it doubles per retry
            min_backoff=15_000,
            # Milliseconds of the longest retry delay, dramatiq caps it at
            # 7 days
            max_backoff=604_800_000,
            # Broker type (currently only rabbitmq supported)
            broker='rabbitmq',
            # RabbitMQ connection settings
            rabbitmq=dict(
                # connection url; use amqps:// to enable tls, verified against
                # the system ca store by default (the secure option)
                url='amqp://localhost:5672/',
                # username/identity, overrides any user in the url
                auth_identity=None,
                # password, overrides any password in the url
                auth_password=None,
                # verify the certificate hostname (amqps only); a string pins
                # the expected name, False skips the check (chain still checked)
                tls_verify_hostname=True,
                # verify the certificate chain (amqps only); False disables it
                tls_verify_cert=True,
                # path to a CA bundle to trust instead of the system store
                tls_ca=None,
                # wait for rabbitmq to confirm each sent message; only then a
                # lost message raises and a deleted queue is declared again
                confirm_delivery=True,
                # highest message priority a queue accepts (x-max-priority, 1 to
                # 255, at most 10 recommended); None declares queues without
                # priorities. A message gets its priority by
                # send_with_options(broker_priority=n). Existing queues keep
                # their argument until they are deleted and declared again
                max_priority=None,
                # milliseconds a dead letter stays in the XQ of every queue
                # (x-message-ttl); None keeps the value of dramatiq, read from
                # the env dramatiq_dead_message_ttl or else 7 days. A value here
                # wins over the env. All senders and workers need the same
                # value, existing XQs keep theirs until deleted and declared
                # again
                dead_message_ttl=None,
            ),
            # Tag for dramatiq locks in the cache
            locks_tag='dramatiq_locks',
            # Prefix for dramatiq lock keys
            locks_key_prefix='dq_',
        )

    def _setup(self, app):
        """
        Cement post-setup hook: bind to app, merge config defaults, attach
        the locks handler if diskcache is loaded, register the broker.

        """
        # save pointer to app
        self.app = app
        # prepare the config
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        # initialize locks handler
        self._locks = None
        # only attach locks when diskcache provides locks_handler; other
        # cache backends (memcached, etc.) don't expose that method, so
        # we skip silently and rely on the locks property to raise on use
        if getattr(self.app._meta, 'cache_handler', None):
            try:
                self._locks = self.app.cache.locks_handler(
                    self._config('locks_tag'),
                    self._config('locks_key_prefix'),
                )
            except AttributeError as e:
                self.app.log.debug(f'dramatiq locks unavailable on cache_handler: {e}')
        # alias dramatiq.actor on the instance: pdoc_pre_render swaps the
        # symbol on both ```dramatiq.actor``` and ```app.dramatiq.actor``` so the
        # generated docs can render decorators that target either path
        self.actor = dramatiq_actor
        # dramatiq register
        self.register()

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a convenient wrapper around the application's config.get method,
        accessing values from the extension's config section.

        ### Args

        - **key** (str): Configuration key to retrieve

        ### Keyword Args

        - **kwargs**: Additional arguments passed to config.get()

        ### Returns

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

        ### Notes

        : Configures a standard set of middleware components including AgeLimit,
            TimeLimit, ShutdownNotifications, Callbacks, Pipelines, Retries,
            CurrentMessage and TokeoAsyncIO (required to run ```async def```
            actors)

        : Creates an ExtendedRabbitmqBroker instance with the configured URL
            and registers it as the global Dramatiq broker

        : Missing keys of the ```rabbitmq``` config take their defaults, so an
            app config written before a new key still works

        : With ```confirm_delivery``` (default) each ```send()``` waits for the
            confirmation of rabbitmq; a message that cannot be delivered
            raises instead of getting lost

        : ```max_priority``` and ```dead_message_ttl``` are arguments of the
            queues (```x-max-priority```, ```x-message-ttl``` of the XQ), so
            all senders and workers need the same values. Rabbitmq refuses
            to declare an existing queue with other arguments; a change
            needs the queue deleted and declared again

        : Without ```dead_message_ttl``` in the config dramatiq keeps its own
            value, read on import from the env ```dramatiq_dead_message_ttl```
            or else 7 days. A value in the config wins over the env and is
            set on the constant of dramatiq, because the module is imported
            before the config is read

        : ```time_limit```, ```max_age```, ```notify_shutdown```,
            ```max_retries```, ```min_backoff``` and ```max_backoff``` are the
            defaults of the middleware for all actors. An actor or a single
            message (```send_with_options```) still sets its own value, the
            message first, then the actor, then the config

        ### Raises

        - **TokeoDramatiqError**: If ```confirm_delivery``` is not a bool,
            ```max_priority``` not None or 1 to 255,
            ```dead_message_ttl``` not None or a positive integer, or a
            middleware default not allowed

        """
        self.app.log.debug('Registering dramatiq middlewares and ExtendedRabbitmqBroker ...')
        # defaults of the middleware for all actors from the config
        time_limit = _config_number('time_limit', self._config('time_limit'), allow_none=False)
        max_age = _config_number('max_age', self._config('max_age'))
        notify_shutdown = self._config('notify_shutdown')
        if not isinstance(notify_shutdown, bool):
            raise TokeoDramatiqError(f'Invalid dramatiq config notify_shutdown: Value {notify_shutdown!r} must be true or false!')
        max_retries = _config_number('max_retries', self._config('max_retries'), min_value=0, allow_none=False)
        min_backoff = _config_number('min_backoff', self._config('min_backoff'), allow_none=False)
        # dramatiq caps the retry delay at 7 days, a longer one has no effect
        max_backoff = _config_number('max_backoff', self._config('max_backoff'), max_value=604_800_000, allow_none=False)
        # re-build set of middlewares to use
        use_middleware = [
            middleware.AgeLimit(max_age=max_age),
            middleware.TimeLimit(time_limit=time_limit),
            middleware.ShutdownNotifications(notify_shutdown=notify_shutdown),
            middleware.Callbacks(),
            middleware.Pipelines(),
            middleware.Retries(max_retries=max_retries, min_backoff=min_backoff, max_backoff=max_backoff),
            middleware.CurrentMessage(),
            TokeoAsyncIO(),
        ]
        # create the broker to RabbitMQ based on config; configured
        # credentials may override any userinfo carried by the url
        # cement does not merge nested defaults, so complete the block here
        rabbitmq_config = {**self._meta.config_defaults['rabbitmq'], **self._config('rabbitmq')}
        if not isinstance(rabbitmq_config['confirm_delivery'], bool):
            raise TokeoDramatiqError(
                f'Invalid dramatiq config rabbitmq.confirm_delivery: Value {rabbitmq_config["confirm_delivery"]!r} must be true or false!'
            )
        max_priority = _config_number('rabbitmq.max_priority', rabbitmq_config['max_priority'], max_value=255)
        dead_message_ttl = _config_number('rabbitmq.dead_message_ttl', rabbitmq_config['dead_message_ttl'])
        # dramatiq reads the env into this constant on import, which already
        # happened, so a configured value goes to the constant directly
        if dead_message_ttl is not None:
            dramatiq_rabbitmq.DEAD_MESSAGE_TTL = dead_message_ttl
        rabbitmq_broker = ExtendedRabbitmqBroker(
            url=rabbitmq_config['url'],
            auth_identity=rabbitmq_config['auth_identity'],
            auth_password=rabbitmq_config['auth_password'],
            tls_verify_hostname=rabbitmq_config['tls_verify_hostname'],
            tls_verify_cert=rabbitmq_config['tls_verify_cert'],
            tls_ca=rabbitmq_config['tls_ca'],
            confirm_delivery=rabbitmq_config['confirm_delivery'],
            max_priority=max_priority,
            middleware=use_middleware,
        )
        # globally set the broker on dramatiq
        dramatiq.set_broker(rabbitmq_broker)
        self.app.log.debug('ExtendedRabbitmqBroker is registered as dramatiq broker')

    def close(self):
        """
        Close the Dramatiq broker connection.

        Called during application shutdown to properly close the connection
        to the RabbitMQ broker, ensuring clean resource release.

        ### Notes

        - Only closes the broker if it's an instance of ExtendedRabbitmqBroker
        - Logs diagnostic messages about the broker closure process

        """
        self.app.log.debug('Closing dramatiq registered broker ...')
        # get the current registered broker
        broker = dramatiq.get_broker()
        # check that is equal to the registered one
        if isinstance(broker, ExtendedRabbitmqBroker):
            # shutdown the connections
            broker.close()
            self.app.log.debug('Closed registered ExtendedRabbitmqBroker')
        else:
            self.app.log.debug('Dramatiq broker instance is not a ExtendedRabbitmqBroker')

    @property
    def locks(self):
        """
        Access the distributed locks handler for rate limiting.

        This property provides access to the distributed locks handler from the
        diskcache extension, which can be used to apply rate limiting and
        concurrency control to Dramatiq actors.

        ### Returns

        - **TokeoDiskCacheLocksHandler**: The locks handler for rate limiting

        ### Raises

        - **TokeoDramatiqError**: If the diskcache extension is not enabled

        ### Example

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
            raise TokeoDramatiqError('Enable tokeo diskcache extension to allow locks')


def _config_number(key, value, min_value=1, max_value=None, allow_none=True):
    """
    Check a number from the dramatiq config.

    ### Args

    - **key** (str): The config key, used in the error message
    - **value** (int|str|None): The value to check
    - **min_value** (int, optional): The lowest allowed number. Defaults to 1
    - **max_value** (int, optional): The highest allowed number. Defaults
        to no limit
    - **allow_none** (bool, optional): Whether None (not set) is allowed.
        Defaults to True

    ### Returns

    - **int|None**: The number as int, or None when not set

    ### Raises

    - **TokeoDramatiqError**: If the value is not an allowed number, or
        None while not allowed

    """
    if value is None and allow_none:
        return None
    try:
        number = None if value is None else _worker_value(value, min_value)
    except ValueError:
        number = None
    if number is None or (max_value is not None and number > max_value):
        expected = f'{min_value} to {max_value}' if max_value is not None else f'an integer >= {min_value}'
        expected = f'null or {expected}' if allow_none else expected
        raise TokeoDramatiqError(f'Invalid dramatiq config {key}: Value {value!r} must be {expected}!')
    return number


def _env_number(name, min_value=1):
    """
    Check a number that dramatiq reads from the environment.

    ### Args

    - **name** (str): The name of the environment variable
    - **min_value** (int, optional): The lowest allowed number. Defaults to 1

    ### Returns

    - **int|None**: The number as int, or None when the variable is not set

    ### Raises

    - **TokeoDramatiqError**: If the variable is set but not an allowed
        number

    """
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        return _worker_value(value, min_value)
    except ValueError:
        raise TokeoDramatiqError(f'Invalid env {name}: Value {value!r} must be an integer >= {min_value}!') from None


def _worker_value(value, min_value=1, allow_auto=False):
    """
    Check a worker option value from config or command line.

    ### Args

    - **value** (int|str): The value to check
    - **min_value** (int, optional): The lowest allowed number. Defaults to 1
    - **allow_auto** (bool, optional): Whether ```'auto'``` is allowed.
        Defaults to False

    ### Returns

    - **int|str**: The number as int, or ```'auto'```

    ### Raises

    - **ValueError**: If the value is not allowed

    """
    try:
        result = as_int_or_auto(value, min_value)
    except ValueError:
        result = None
    if result is None or (result == 'auto' and not allow_auto):
        expected = f'"auto" or an integer >= {min_value}' if allow_auto else f'an integer >= {min_value}'
        raise ValueError(f'Value {value!r} must be {expected}!')
    return result


def _worker_argtype(allow_auto=False):
    """
    Build an argparse type for a worker option of the serve command.

    ### Args

    - **allow_auto** (bool, optional): Whether ```'auto'``` is allowed.
        Defaults to False

    ### Returns

    - **callable**: The type function, raising ```ArgumentTypeError```

    """

    def argtype(value):
        try:
            return _worker_value(value, allow_auto=allow_auto)
        except ValueError as e:
            # argparse shows the message of this error instead of a generic one
            raise argparse.ArgumentTypeError(str(e)) from None

    return argtype


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
        Get a worker option from the command line or else from the config.

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

        - **TokeoDramatiqError**: If the config value is not allowed

        """
        # a given switch replaces the config value, argparse checked it already
        value = getattr(self.app.pargs, option)
        if value is not None:
            return value
        try:
            value = _worker_value(self.app.dramatiq.config(key), config_min_value, allow_auto)
        except ValueError as e:
            raise TokeoDramatiqError(f'Invalid dramatiq config {key}: {e}') from None
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
                    type=_worker_argtype(allow_auto=True),
                    metavar='N|auto',
                    help='number of worker processes, auto follows the dramatiq defaults',
                ),
            ),
            (
                ['-t', '--threads'],
                dict(
                    type=_worker_argtype(),
                    metavar='N',
                    help='number of worker threads per process',
                ),
            ),
            (
                ['--queue-prefetch'],
                dict(
                    type=_worker_argtype(allow_auto=True),
                    metavar='N|auto',
                    help='messages to prefetch per queue, auto follows the dramatiq defaults',
                ),
            ),
            (
                ['--delay-queue-prefetch'],
                dict(
                    type=_worker_argtype(allow_auto=True),
                    metavar='N|auto',
                    help='messages to prefetch per delay queue, auto follows the dramatiq defaults',
                ),
            ),
            (
                ['--worker-shutdown-timeout'],
                dict(
                    type=_worker_argtype(),
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

        - **TokeoDramatiqError**: If a worker option or ```use_spawn``` in
            the config is not allowed

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
        worker_poll_interval = _config_number('worker_poll_interval', self.app.dramatiq.config('worker_poll_interval'))
        if worker_poll_interval is None:
            # dramatiq takes the env then, check it before the workers start
            _env_number('dramatiq_worker_timeout')
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


def tokeo_dramatiq_pdoc_pre_render(app):
    """
    Replace the dramatiq decorator with a simple one for pdoc rendering.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

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

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

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

    ### Args

    - **app** (Application): The Cement application instance
    - **func** (function): The function being decorated
    - **decorator** (str): The decorator string
    - **args** (list): Positional arguments to the decorator

    ### Keyword Args

    - **kwargs** (dict): Keyword arguments to the decorator

    ### Returns

    - **dict|None**: Dictionary with decorator information or None if not handled

    ### Notes

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

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

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

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

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

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    - Registers the TokeoDramatiqController for CLI commands
    - Defines extension-specific hooks that can be used by other extensions
    - Sets up integration with pdoc for documentation generation

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
