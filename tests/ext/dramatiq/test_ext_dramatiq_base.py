"""
Tests for tokeo.ext.dramatiq.base without a running broker.

They check the broker, the middleware and the config values that register
hands over to dramatiq.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
import dramatiq
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dramatiq import broker as dramatiq_broker
from dramatiq.asyncio import get_event_loop_thread
from dramatiq.brokers import rabbitmq as dramatiq_rabbitmq
from tokeo.ext.dramatiq import TokeoDramatiqConfigError, TokeoAsyncIO
from tests.ext.dramatiq.lib.dramatiq_tests_helper import DramatiqTest, DramatiqBrokerTest, DramatiqMiddlewareTest


@pytest.fixture(autouse=True)
def keep_global_broker(monkeypatch):
    """The global broker of dramatiq, restored after the test, register sets it."""
    monkeypatch.setattr(dramatiq_broker, 'global_broker', dramatiq_broker.global_broker)


@pytest.fixture
def keep_dead_message_ttl(monkeypatch):
    """Restores DEAD_MESSAGE_TTL of dramatiq after the test, register sets it."""
    monkeypatch.setattr(dramatiq_rabbitmq, 'DEAD_MESSAGE_TTL', dramatiq_rabbitmq.DEAD_MESSAGE_TTL)
    return dramatiq_rabbitmq.DEAD_MESSAGE_TTL


def _ca_cert(name):
    # a self-signed certificate in pem, enough as ca file of the broker
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# --- asyncio middleware --------------------------------------------------------


def test_asyncio_event_loop_thread_is_daemon():
    """The event loop thread is a daemon and stops on shutdown."""
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


def test_register_uses_tokeo_asyncio(tmp):
    """register adds TokeoAsyncIO to the broker."""
    with DramatiqTest(tmp, []):
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
def test_register_confirm_delivery(tmp, rabbitmq, confirm):
    """confirm_delivery is on by default and follows the config."""
    with DramatiqBrokerTest(tmp, rabbitmq):
        assert dramatiq.get_broker().confirm_delivery is confirm


def test_register_confirm_delivery_with_tls_parameters(tmp):
    """amqps with a ca builds pika parameters with ssl, confirm stays on."""
    rabbitmq = dict(url='amqps://localhost:5671/', tls_ca=os.path.join(tmp.dir, 'ca.pem'))
    with open(rabbitmq['tls_ca'], 'w') as f:
        f.write(_ca_cert('localhost'))
    with DramatiqBrokerTest(tmp, rabbitmq):
        broker = dramatiq.get_broker()
        assert broker.parameters[0].ssl_options is not None
        assert broker.confirm_delivery is True


@pytest.mark.parametrize('value', ['yes', 1, None])
def test_register_invalid_confirm_delivery(tmp, value):
    """confirm_delivery other than a bool raises the config error."""
    with pytest.raises(TokeoDramatiqConfigError, match='Invalid dramatiq config rabbitmq.confirm_delivery'):
        with DramatiqBrokerTest(tmp, dict(confirm_delivery=value)):
            pass


@pytest.mark.parametrize('rabbitmq', [None, dict(url='amqp://localhost:5672/')], ids=['default', 'partial_block'])
def test_register_queue_arguments_default(tmp, keep_dead_message_ttl, rabbitmq):
    """Without config no priority and the ttl of dramatiq stay."""
    with DramatiqBrokerTest(tmp, rabbitmq):
        broker = dramatiq.get_broker()
        # without config the queues look as before: no priority, ttl of dramatiq
        assert broker.max_priority is None
        assert 'x-max-priority' not in broker._build_queue_arguments('mail')
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == keep_dead_message_ttl


@pytest.mark.parametrize(
    'queue, prefetch, expected',
    [
        ('orders_unparalleled', 8, 1),
        ('Orders_UNPARALLELED', 8, 1),
        # the delay queue keeps its prefetch for the delayed messages
        ('orders_unparalleled.DQ', 8000, 8000),
        ('mail', 8, 8),
    ],
)
def test_unparalleled_queues_consume_one_message_at_a_time(tmp, monkeypatch, queue, prefetch, expected):
    """An unparalleled queue prefetches 1, its delay queue as given."""
    consumed = []
    monkeypatch.setattr(
        dramatiq_rabbitmq.RabbitmqBroker, 'consume', lambda self, name, prefetch=1, timeout=5000: consumed.append(prefetch)
    )
    with DramatiqBrokerTest(tmp, None) as app:
        app.dramatiq.broker.consume(queue, prefetch=prefetch)
    assert consumed == [expected]


@pytest.mark.usefixtures('keep_dead_message_ttl')
def test_register_queue_arguments_from_config(tmp):
    """max_priority and dead_message_ttl from the config reach the queues."""
    with DramatiqBrokerTest(tmp, dict(max_priority=10, dead_message_ttl='86400000')):
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
def test_register_invalid_queue_arguments(tmp, keep_dead_message_ttl, key, value):
    """Invalid queue arguments raise and leave the ttl as it was."""
    with pytest.raises(TokeoDramatiqConfigError, match=f'Invalid dramatiq config rabbitmq.{key}'):
        with DramatiqBrokerTest(tmp, {key: value}):
            pass
    assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == keep_dead_message_ttl


# --- middleware defaults -----------------------------------------------------


def middleware_of(kind):
    return [m for m in dramatiq.get_broker().middleware if isinstance(m, kind)][0]


def test_register_middleware_defaults(tmp):
    """The middleware keeps the defaults of dramatiq."""
    from dramatiq import middleware

    with DramatiqMiddlewareTest(tmp):
        # the defaults are the ones of dramatiq, so nothing changes for apps
        assert middleware_of(middleware.TimeLimit).time_limit == 600_000
        assert middleware_of(middleware.AgeLimit).max_age is None
        assert middleware_of(middleware.ShutdownNotifications).notify_shutdown is False
        retries = middleware_of(middleware.Retries)
        assert (retries.max_retries, retries.min_backoff, retries.max_backoff) == (20, 15_000, 604_800_000)


def test_register_middleware_from_config(tmp):
    """The config sets time limit, age, shutdown notice and retries."""
    from dramatiq import middleware

    config = dict(time_limit=5000, max_age='60000', notify_shutdown=True, max_retries=0, min_backoff=200, max_backoff=1000)
    with DramatiqMiddlewareTest(tmp, **config):
        assert middleware_of(middleware.TimeLimit).time_limit == 5000
        assert middleware_of(middleware.AgeLimit).max_age == 60000
        assert middleware_of(middleware.ShutdownNotifications).notify_shutdown is True
        retries = middleware_of(middleware.Retries)
        assert (retries.max_retries, retries.min_backoff, retries.max_backoff) == (0, 200, 1000)


def test_register_actor_options_stay_available(tmp):
    """Actors may still set the options the config defaults."""
    with DramatiqMiddlewareTest(tmp, time_limit=5000):
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
def test_register_invalid_middleware_defaults(tmp, key, value):
    """An invalid middleware value raises the config error."""
    with pytest.raises(TokeoDramatiqConfigError, match=f'Invalid dramatiq config {key}'):
        with DramatiqMiddlewareTest(tmp, **{key: value}):
            pass


# --- dead message ttl env ------------------------------------------------------


def test_register_dead_message_ttl_valid_env(tmp, keep_dead_message_ttl, monkeypatch):
    """A valid dramatiq_dead_message_ttl env is accepted as it is."""
    monkeypatch.setenv('dramatiq_dead_message_ttl', '86400000')
    with DramatiqBrokerTest(tmp, None):
        # dramatiq read the env on import already, tokeo only checks it
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == keep_dead_message_ttl


@pytest.mark.usefixtures('keep_dead_message_ttl')
@pytest.mark.parametrize('value', ['0', '-1', 'week'])
def test_register_dead_message_ttl_invalid_env(tmp, monkeypatch, value):
    """An invalid dramatiq_dead_message_ttl env raises the config error."""
    monkeypatch.setenv('dramatiq_dead_message_ttl', value)
    with pytest.raises(TokeoDramatiqConfigError, match='Invalid env dramatiq_dead_message_ttl'):
        with DramatiqBrokerTest(tmp, None):
            pass


@pytest.mark.usefixtures('keep_dead_message_ttl')
def test_register_dead_message_ttl_config_wins_over_env(tmp, monkeypatch):
    """The config wins over the env, even over an invalid one."""
    monkeypatch.setenv('dramatiq_dead_message_ttl', '0')
    with DramatiqBrokerTest(tmp, dict(dead_message_ttl=60000)):
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == 60000


# --- actor send wrapper --------------------------------------------------------


def test_actor_alias_declares_tokeo_actor(tmp):
    """app.dramatiq.actor makes a TokeoActor, an own actor_class stays."""
    from tokeo.ext.dramatiq import TokeoActor

    with DramatiqBrokerTest(tmp, None) as app:
        # both forms of the decorator, the bare one and with options
        bare = app.dramatiq.actor(lambda: None, actor_name='bare_actor')
        with_options = app.dramatiq.actor(queue_name='mail', actor_name='mail_actor')(lambda: None)
        own = app.dramatiq.actor(lambda: None, actor_name='own_actor', actor_class=dramatiq.Actor)
    assert type(bare) is TokeoActor and type(with_options) is TokeoActor
    assert with_options.queue_name == 'mail'
    # an own actor_class stays possible
    assert type(own) is dramatiq.Actor


def test_actor_send_and_run_unchanged(tmp, monkeypatch):
    """send, send_with_options and the call of an actor work as before."""
    with DramatiqBrokerTest(tmp, None) as app:
        actor = app.dramatiq.actor(lambda value: value * 2, actor_name='double')
        sent = []
        monkeypatch.setattr(app.dramatiq.broker, 'enqueue', lambda message, delay=None: sent.append((message, delay)) or message)
        message = actor.send_with_options(args=(21,), delay=500, max_retries=1)
        actor.send(2)
    assert (message.args, message.options['max_retries'], sent[0][1]) == ((21,), 1, 500)
    assert len(sent) == 2
    # the worker calls the actor itself, that path is untouched
    assert actor(21) == 42


def test_actor_send_without_rabbitmq(tmp, monkeypatch):
    """A send without rabbitmq raises the connection error naming the actor."""
    from dramatiq.errors import BrokerConnectionError, ConnectionClosed
    from tokeo.core.exc import TokeoError
    from tokeo.ext.dramatiq import TokeoDramatiqConnectionError

    def refused(message, delay=None):
        raise ConnectionClosed('connection refused')

    with DramatiqBrokerTest(tmp, dict(url='amqp://user:secret@mq.example:5673/')) as app:
        actor = app.dramatiq.actor(lambda: None, actor_name='count_words')
        monkeypatch.setattr(app.dramatiq.broker, 'enqueue', refused)
        with pytest.raises(TokeoDramatiqConnectionError) as e:
            actor.send()
    assert str(e.value) == 'Cannot send count_words to rabbitmq at mq.example:5673 (ConnectionClosed: connection refused)!'
    # main catches it as tokeo error, code catching dramatiq errors still does
    assert isinstance(e.value, TokeoError) and isinstance(e.value, ConnectionClosed)
    assert isinstance(e.value, BrokerConnectionError) and e.value.args[0] == str(e.value)
    assert (e.value.address, e.value.actor_name) == ('mq.example:5673', 'count_words')
    assert e.value.cause == 'ConnectionClosed: connection refused'
    assert isinstance(e.value.__cause__, ConnectionClosed)


def test_pdoc_hooks_restore_the_tokeo_actor(tmp):
    """The pdoc hooks swap the actor decorator and restore it after."""
    from tokeo.core.utils.pdoc import pdoc_replace_decorator
    from tokeo.ext.dramatiq import tokeo_actor, tokeo_dramatiq_pdoc_pre_render, tokeo_dramatiq_pdoc_post_render

    original = dramatiq.actor
    with DramatiqBrokerTest(tmp, None) as app:
        assert app.dramatiq.actor is tokeo_actor
        tokeo_dramatiq_pdoc_pre_render(app)
        # pdoc renders both decorator forms by the simple stub
        assert dramatiq.actor is pdoc_replace_decorator and app.dramatiq.actor is pdoc_replace_decorator
        tokeo_dramatiq_pdoc_post_render(app)
        assert app.dramatiq.actor is tokeo_actor
    assert dramatiq.actor is original
