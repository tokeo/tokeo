"""
Tests for tokeo.ext.dramatiq.base without a running broker.

They check the broker, the middleware and the config values that register
hands over to dramatiq.
"""

import os

import pytest
from dramatiq.asyncio import get_event_loop_thread
from dramatiq.brokers import rabbitmq as dramatiq_rabbitmq
from tokeo.ext.dramatiq import TokeoDramatiqError, TokeoAsyncIO
from tests.ext.test_ext_smtp import make_cert

# --- asyncio middleware --------------------------------------------------------


def test_asyncio_event_loop_thread_is_daemon():
    asyncio_middleware = TokeoAsyncIO()
    asyncio_middleware.before_worker_boot(None, None)
    try:
        thread = get_event_loop_thread()
        # a killed worker process must not wait for the loop thread
        assert thread.daemon is True
        assert thread.is_alive()
    finally:
        asyncio_middleware.after_worker_shutdown(None, None)
    # the normal shutdown still stops and joins the thread
    assert get_event_loop_thread() is None
    assert not thread.is_alive()


def test_register_uses_tokeo_asyncio(tmp, dramatiq_app):
    import dramatiq

    with dramatiq_app(tmp, []):
        kinds = [type(m) for m in dramatiq.get_broker().middleware]
    assert TokeoAsyncIO in kinds


# --- broker ------------------------------------------------------------------


@pytest.mark.parametrize(
    'rabbitmq, confirm',
    [
        (None, True),
        # a config block written before the key still gets the default
        (dict(url='amqp://localhost:5672/'), True),
        (dict(confirm_delivery=False), False),
        (dict(confirm_delivery=True), True),
    ],
    ids=['default', 'partial_block', 'off', 'on'],
)
def test_register_confirm_delivery(tmp, rabbitmq, confirm, broker_app):
    import dramatiq

    with broker_app(tmp, rabbitmq):
        assert dramatiq.get_broker().confirm_delivery is confirm


def test_register_confirm_delivery_with_tls_parameters(tmp, broker_app):
    import dramatiq

    # amqps with a custom ca builds pika parameters instead of the url
    rabbitmq = dict(url='amqps://localhost:5671/', tls_ca=os.path.join(tmp.dir, 'ca.pem'))
    with open(rabbitmq['tls_ca'], 'w') as f:
        f.write(make_cert('localhost')[0])
    with broker_app(tmp, rabbitmq):
        broker = dramatiq.get_broker()
        assert broker.parameters[0].ssl_options is not None
        assert broker.confirm_delivery is True


@pytest.mark.parametrize('value', ['yes', 1, None])
def test_register_invalid_confirm_delivery(tmp, value, broker_app):
    with pytest.raises(TokeoDramatiqError, match='Invalid dramatiq config rabbitmq.confirm_delivery'):
        with broker_app(tmp, dict(confirm_delivery=value)):
            pass


@pytest.mark.parametrize('rabbitmq', [None, dict(url='amqp://localhost:5672/')], ids=['default', 'partial_block'])
def test_register_queue_arguments_default(tmp, keep_dead_message_ttl, rabbitmq, broker_app):
    import dramatiq

    with broker_app(tmp, rabbitmq):
        broker = dramatiq.get_broker()
        # without config the queues look as before: no priority, ttl of dramatiq
        assert broker.max_priority is None
        assert 'x-max-priority' not in broker._build_queue_arguments('mail')
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == keep_dead_message_ttl


def test_register_queue_arguments_from_config(tmp, keep_dead_message_ttl, broker_app):
    import dramatiq

    with broker_app(tmp, dict(max_priority=10, dead_message_ttl='86400000')):
        broker = dramatiq.get_broker()
        assert broker.max_priority == 10
        assert broker._build_queue_arguments('mail')['x-max-priority'] == 10
        # the priority also reaches queues with single active consumer
        arguments = broker._build_queue_arguments('mail_unparalleled')
        assert (arguments['x-max-priority'], arguments['x-single-active-consumer']) == (10, True)
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == 86400000


@pytest.mark.parametrize(
    'key, value',
    [
        ('max_priority', 0),
        ('max_priority', 256),
        ('max_priority', 'high'),
        ('dead_message_ttl', 0),
        ('dead_message_ttl', 'week'),
        ('dead_message_ttl', True),
    ],
)
def test_register_invalid_queue_arguments(tmp, keep_dead_message_ttl, key, value, broker_app):
    with pytest.raises(TokeoDramatiqError, match=f'Invalid dramatiq config rabbitmq.{key}'):
        with broker_app(tmp, {key: value}):
            pass
    assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == keep_dead_message_ttl


# --- middleware defaults -----------------------------------------------------


def middleware_of(kind):
    import dramatiq

    return [m for m in dramatiq.get_broker().middleware if isinstance(m, kind)][0]


def test_register_middleware_defaults(tmp, middleware_app):
    from dramatiq import middleware

    with middleware_app(tmp):
        # the defaults are the ones of dramatiq, so nothing changes for apps
        assert middleware_of(middleware.TimeLimit).time_limit == 600_000
        assert middleware_of(middleware.AgeLimit).max_age is None
        assert middleware_of(middleware.ShutdownNotifications).notify_shutdown is False
        retries = middleware_of(middleware.Retries)
        assert (retries.max_retries, retries.min_backoff, retries.max_backoff) == (20, 15_000, 604_800_000)


def test_register_middleware_from_config(tmp, middleware_app):
    from dramatiq import middleware

    config = dict(time_limit=5000, max_age='60000', notify_shutdown=True, max_retries=0, min_backoff=200, max_backoff=1000)
    with middleware_app(tmp, **config):
        assert middleware_of(middleware.TimeLimit).time_limit == 5000
        assert middleware_of(middleware.AgeLimit).max_age == 60000
        assert middleware_of(middleware.ShutdownNotifications).notify_shutdown is True
        retries = middleware_of(middleware.Retries)
        assert (retries.max_retries, retries.min_backoff, retries.max_backoff) == (0, 200, 1000)


def test_register_actor_options_stay_available(tmp, middleware_app):
    import dramatiq

    with middleware_app(tmp, time_limit=5000):
        options = dramatiq.get_broker().actor_options
    # actors and messages still override the defaults of the config
    for name in ('time_limit', 'max_age', 'notify_shutdown', 'max_retries', 'min_backoff', 'max_backoff'):
        assert name in options


@pytest.mark.parametrize(
    'key, value',
    [
        ('time_limit', None),
        ('time_limit', 0),
        ('time_limit', 'auto'),
        ('max_age', 0),
        ('notify_shutdown', 'yes'),
        ('max_retries', -1),
        ('max_retries', None),
        ('min_backoff', 0),
        ('max_backoff', 604_800_001),
    ],
)
def test_register_invalid_middleware_defaults(tmp, key, value, middleware_app):
    with pytest.raises(TokeoDramatiqError, match=f'Invalid dramatiq config {key}'):
        with middleware_app(tmp, **{key: value}):
            pass


# --- dead message ttl env ------------------------------------------------------


def test_register_dead_message_ttl_valid_env(tmp, keep_dead_message_ttl, monkeypatch, broker_app):
    monkeypatch.setenv('dramatiq_dead_message_ttl', '86400000')
    with broker_app(tmp, None):
        # dramatiq read the env on import already, tokeo only checks it
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == keep_dead_message_ttl


@pytest.mark.parametrize('value', ['0', '-1', 'week'])
def test_register_dead_message_ttl_invalid_env(tmp, keep_dead_message_ttl, monkeypatch, value, broker_app):
    monkeypatch.setenv('dramatiq_dead_message_ttl', value)
    with pytest.raises(TokeoDramatiqError, match='Invalid env dramatiq_dead_message_ttl'):
        with broker_app(tmp, None):
            pass


def test_register_dead_message_ttl_config_wins_over_env(tmp, keep_dead_message_ttl, monkeypatch, broker_app):
    # with the config value the env does not matter, even an invalid one
    monkeypatch.setenv('dramatiq_dead_message_ttl', '0')
    with broker_app(tmp, dict(dead_message_ttl=60000)):
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == 60000
