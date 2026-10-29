"""
Tokeo Dramatiq extension base.

Everything behind ```app.dramatiq```: the actor wrapper, the asyncio
middleware, the RabbitMQ broker with single-active-consumer queues and the
```TokeoDramatiq``` object that reads the config, registers the broker with
its middleware and gives access to the diskcache locks.

"""

import os
from cement.core.meta import MetaMixin
import dramatiq
from dramatiq import middleware, actor as dramatiq_actor
from dramatiq.errors import BrokerConnectionError
from dramatiq.asyncio import EventLoopThread, set_event_loop_thread
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.brokers import rabbitmq as dramatiq_rabbitmq
from dramatiq.common import q_name
import pika
from tokeo.core.utils.url import overload_with_auth as url_overload_with_auth
from tokeo.core.utils.tls import create_ssl_context as tls_create_ssl_context
from tokeo.core.utils.base import as_int_or_auto
from tokeo.ext.dramatiq.exc import TokeoDramatiqError, TokeoDramatiqConfigError, TokeoDramatiqConnectionError


class TokeoActor(dramatiq.Actor):
    """
    Actor whose send raises ```TokeoDramatiqConnectionError``` without rabbitmq.

    Only sending changes, the worker runs the actor as before.

    """

    def send_with_options(self, *, args=(), kwargs=None, delay=None, **options):
        """Send a message, with a tokeo error if rabbitmq is not reachable."""
        try:
            return super().send_with_options(args=args, kwargs=kwargs, delay=delay, **options)
        except BrokerConnectionError as e:
            # another broker, e.g. the stub broker of tests, has no address
            raise TokeoDramatiqConnectionError(getattr(self.broker, 'address', None), e, actor_name=self.actor_name) from e


def tokeo_actor(fn=None, **options):
    """
    Declare an actor like ```dramatiq.actor```, as ```TokeoActor``` by default.

    ### Args

    - **fn** (callable, optional): The function of the actor

    ### Keyword Args

    - **options**: The options of ```dramatiq.actor```; an own
        ```actor_class``` replaces ```TokeoActor```

    ### Returns

    - **TokeoActor|callable**: The actor, or the decorator without fn

    """
    options.setdefault('actor_class', TokeoActor)
    return dramatiq_actor(fn, **options)


class TokeoAsyncIO(middleware.AsyncIO):
    """
    AsyncIO middleware whose event loop thread does not block the exit.

    A worker killed by a second SIGTERM would otherwise hang on the non-daemon
    event loop thread of the standard middleware.

    ### Notes

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

    A queue whose name contains '_unparalleled' is declared with the RabbitMQ
    single-active-consumer flag and consumed with a prefetch of 1: only one
    worker process consumes it, one message at a time.

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

    ### Notes

    : Implementation based on solution from the Dramatiq users group:
        https://groups.io/g/dramatiq-users/topic/77913723

    : Also accepts auth_identity/auth_password and merges them into the
        connection url, since RabbitmqBroker cannot take a url and separate
        pika credentials at the same time

    : For amqps urls it accepts tls_verify_hostname/tls_verify_cert/tls_ca to
        customize the ssl context; a custom context forces the pika
        parameters path, since RabbitmqBroker cannot mix url and parameters

    """

    def __init__(
        self, *, url=None, auth_identity=None, auth_password=None, tls_verify_hostname=True, tls_verify_cert=True, tls_ca=None, **kwargs
    ):
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

    @property
    def address(self):
        """Host and port of the broker, never its url with the password."""
        parameters = self.parameters if isinstance(self.parameters, list) else [self.parameters]
        return ', '.join(f'{p.host}:{p.port}' for p in parameters)

    def consume(self, queue_name, prefetch=1, timeout=5000):
        """
        Create a consumer, with a prefetch of 1 for unparalleled queues.

        ### Args

        - **queue_name** (str): The queue to consume
        - **prefetch** (int, optional): Messages to fetch ahead. Defaults to 1
        - **timeout** (int, optional): Milliseconds to wait for a message.
            Defaults to 5000

        ### Returns

        - **Consumer**: The consumer of the queue

        """
        # one message at a time; their delay queues keep the prefetch, a far
        # delayed message would hold back the earlier ones otherwise
        if '_unparalleled' in queue_name.lower() and queue_name == q_name(queue_name):
            prefetch = 1
        return super().consume(queue_name, prefetch=prefetch, timeout=timeout)

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
            # Number of worker processes to run (auto: number of cpus)
            worker_processes=2,
            # Worker shutdown timeout in milliseconds
            worker_shutdown_timeout=600_000,
            # Delay between worker restarts in milliseconds
            restart_delay=3_000,
            # Worker poll interval in milliseconds (None: dramatiq default)
            worker_poll_interval=None,
            # Number of messages to prefetch from queues (auto: dramatiq default)
            queue_prefetch='auto',
            # Number of delay queue messages to prefetch (auto: dramatiq default)
            delay_queue_prefetch='auto',
            # Start the worker processes by spawn instead of forkserver
            use_spawn=False,
            # Time limit per message in milliseconds
            time_limit=600_000,
            # Max age of a message in milliseconds (None: no limit)
            max_age=None,
            # Raise Shutdown in running actors when the worker stops
            notify_shutdown=False,
            # Max retries of a failed message
            max_retries=20,
            # Min delay of a retry in milliseconds
            min_backoff=15_000,
            # Max delay of a retry in milliseconds (at most 7 days)
            max_backoff=604_800_000,
            # Send a heartbeat of each worker process for the monitor
            monitoring=False,
            # Seconds between two heartbeats
            monitoring_interval=5,
            # Send task arguments and error texts: off, on or debug (with --debug)
            monitoring_with_details='debug',
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
                # wait for rabbitmq to confirm each sent message
                confirm_delivery=True,
                # highest message priority of the queues (None: no priorities)
                max_priority=None,
                # lifetime of dead letters in milliseconds (None: dramatiq default)
                dead_message_ttl=None,
            ),
            # Tag for dramatiq locks in the cache
            locks_tag='dramatiq_locks',
            # Prefix for dramatiq lock keys
            locks_key_prefix='dq_',
        )

    #: (Application) The Cement application instance, set on setup
    app = None

    #: (callable) The actor decorator of tokeo, as ```dramatiq.actor```, set on
    #: setup
    actor = None

    #: (TokeoDramatiqQueues) Queue state and maintenance, set on setup
    queues = None

    #: (TokeoDramatiqMonitor) Workers and queues of the app, set on setup
    monitor = None

    #: (ExtendedRabbitmqBroker) The registered broker, set on register
    broker = None

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
        self.actor = tokeo_actor
        # dramatiq register
        self.register()
        # imported here, the queues and monitoring modules build on this one
        from tokeo.ext.dramatiq.queues import TokeoDramatiqQueues
        from tokeo.ext.dramatiq.monitoring import TokeoDramatiqMonitor

        self.queues = TokeoDramatiqQueues(self)
        self.monitor = TokeoDramatiqMonitor(self)

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

        ### Raises

        - **TokeoDramatiqConfigError**: If a config value or a dramatiq env is
            not allowed

        ### Notes

        : Configures the middleware AgeLimit, TimeLimit, ShutdownNotifications,
            Callbacks, Pipelines, Retries, CurrentMessage and TokeoAsyncIO,
            with ```monitoring``` also TokeoDramatiqHeartbeat

        : Creates an ExtendedRabbitmqBroker instance with the configured URL
            and registers it as the global Dramatiq broker

        : Missing keys of the ```rabbitmq``` config take their defaults

        """
        self.app.log.debug('Registering dramatiq middlewares and ExtendedRabbitmqBroker ...')
        # defaults of the middleware for all actors from the config
        time_limit = _number('config', 'time_limit', self._config('time_limit'))
        max_age = _number('config', 'max_age', self._config('max_age'), allow_none=True)
        notify_shutdown = self._config('notify_shutdown')
        if not isinstance(notify_shutdown, bool):
            raise TokeoDramatiqConfigError('config', 'notify_shutdown', notify_shutdown, 'true or false')
        max_retries = _number('config', 'max_retries', self._config('max_retries'), min_value=0)
        min_backoff = _number('config', 'min_backoff', self._config('min_backoff'))
        # dramatiq caps the retry delay at 7 days, a longer one has no effect
        max_backoff = _number('config', 'max_backoff', self._config('max_backoff'), max_value=604_800_000)
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
        heartbeat = self._heartbeat()
        if heartbeat is not None:
            use_middleware.append(heartbeat)
        # create the broker to RabbitMQ based on config; configured
        # credentials may override any userinfo carried by the url
        # cement does not merge nested defaults, so complete the block here
        rabbitmq_config = {**self._meta.config_defaults['rabbitmq'], **self._config('rabbitmq')}
        if not isinstance(rabbitmq_config['confirm_delivery'], bool):
            raise TokeoDramatiqConfigError('config', 'rabbitmq.confirm_delivery', rabbitmq_config['confirm_delivery'], 'true or false')
        max_priority = _number('config', 'rabbitmq.max_priority', rabbitmq_config['max_priority'], max_value=255, allow_none=True)
        dead_message_ttl = _number('config', 'rabbitmq.dead_message_ttl', rabbitmq_config['dead_message_ttl'], allow_none=True)
        # dramatiq read the env on import already, so set its constant
        if dead_message_ttl is not None:
            dramatiq_rabbitmq.DEAD_MESSAGE_TTL = dead_message_ttl
        else:
            # dramatiq takes the env then; 0 would drop every dead letter
            if 'dramatiq_dead_message_ttl' in os.environ:
                _number('env', 'dramatiq_dead_message_ttl', os.environ['dramatiq_dead_message_ttl'])
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
        self.broker = rabbitmq_broker
        dramatiq.set_broker(rabbitmq_broker)
        self.app.log.debug('ExtendedRabbitmqBroker is registered as dramatiq broker')

    def _heartbeat(self):
        """Build the heartbeat middleware, None without monitoring."""
        monitoring = self._config('monitoring')
        if not isinstance(monitoring, bool):
            raise TokeoDramatiqConfigError('config', 'monitoring', monitoring, 'true or false')
        interval = _number('config', 'monitoring_interval', self._config('monitoring_interval'))
        details = _details(self._config('monitoring_with_details'), self.app.debug)
        if not monitoring:
            return None
        # imported here, the monitoring module builds on this one
        from tokeo.ext.dramatiq.monitoring import TokeoDramatiqHeartbeat, exchange_name, _app_version

        app = dict(label=self.app._meta.label, version=_app_version(self.app))
        return TokeoDramatiqHeartbeat(exchange_name(self.app), interval, details, app)

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
        if getattr(self, 'queues', None) is not None:
            self.queues.close()
        if getattr(self, 'monitor', None) is not None:
            self.monitor.close()
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


def _number(source, name, value, min_value=1, max_value=None, allow_none=False, allow_auto=False):
    """
    Check a number from the config, an env or a switch of the extension.

    ### Args

    - **source** (str): Where the value comes from, ```config```, ```env```
        or ```switch```
    - **name** (str): The key, env or switch of the value
    - **value** (int|str|None): The value to check
    - **min_value** (int, optional): The lowest allowed number. Defaults to 1
    - **max_value** (int, optional): The highest allowed number. Defaults
        to no limit
    - **allow_none** (bool, optional): Whether None (not set) is allowed.
        Defaults to False
    - **allow_auto** (bool, optional): Whether ```'auto'``` is allowed.
        Defaults to False

    ### Returns

    - **int|str|None**: The number as int, ```'auto'```, or None when not set

    ### Raises

    - **TokeoDramatiqConfigError**: If the value is not allowed

    """
    if value is None and allow_none:
        return None
    try:
        number = None if value is None else as_int_or_auto(value, min_value)
    except ValueError:
        number = None
    if number == 'auto':
        valid = allow_auto
    else:
        valid = number is not None and (max_value is None or number <= max_value)
    if not valid:
        expected = f'{min_value} to {max_value}' if max_value is not None else f'an integer >= {min_value}'
        expected = f'"auto" or {expected}' if allow_auto else expected
        expected = f'null or {expected}' if allow_none else expected
        raise TokeoDramatiqConfigError(source, name, value, expected)
    return number


def _details(value, debug):
    """
    Read the config ```monitoring_with_details```.

    ### Args

    - **value** (bool|str|None): The config value
    - **debug** (bool): Whether the app runs with --debug

    ### Returns

    - **bool**: Whether the heartbeats carry the details

    ### Raises

    - **TokeoDramatiqConfigError**: If the value is not allowed

    """
    # yaml reads unquoted on, off, yes and no as booleans already
    text = 'null' if value is None else str(value).lower()
    if text in ('true', 'on', 'yes'):
        return True
    if text in ('false', 'off', 'no', 'null'):
        return False
    if text == 'debug':
        return bool(debug)
    raise TokeoDramatiqConfigError('config', 'monitoring_with_details', value, 'off, on or debug')
