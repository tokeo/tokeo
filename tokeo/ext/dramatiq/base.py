"""
Tokeo Dramatiq extension base.

Everything behind ```app.dramatiq```: the extension error, the asyncio
middleware, the RabbitMQ broker with single-active-consumer queues and the
```TokeoDramatiq``` object that reads the config, registers the broker with
its middleware and gives access to the diskcache locks.

"""

import os
from cement.core.meta import MetaMixin
import dramatiq
from dramatiq import middleware, actor as dramatiq_actor
from dramatiq.asyncio import EventLoopThread, set_event_loop_thread
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.brokers import rabbitmq as dramatiq_rabbitmq
import pika
from tokeo.core.exc import TokeoError
from tokeo.core.utils.url import overload_with_auth as url_overload_with_auth
from tokeo.core.utils.tls import create_ssl_context as tls_create_ssl_context
from tokeo.core.utils.base import as_int_or_auto


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
            before the config is read. Without the config value an env set
            from outside is checked; a non number fails earlier, when
            dramatiq gets imported

        : ```time_limit```, ```max_age```, ```notify_shutdown```,
            ```max_retries```, ```min_backoff``` and ```max_backoff``` are the
            defaults of the middleware for all actors. An actor or a single
            message (```send_with_options```) still sets its own value, the
            message first, then the actor, then the config

        ### Raises

        - **TokeoDramatiqError**: If ```confirm_delivery``` is not a bool,
            ```max_priority``` not None or 1 to 255,
            ```dead_message_ttl``` not None or a positive integer, the env
            ```dramatiq_dead_message_ttl``` not a positive integer while
            used, or a middleware default not allowed

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
        else:
            # dramatiq takes the env then; 0 would drop every dead letter
            _env_number('dramatiq_dead_message_ttl')
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
