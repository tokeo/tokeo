"""
Tests for tokeo.ext.dramatiq.queues without a running broker.

A small fake of pika keeps the queues in memory, so the tests check what the
queue management reads, moves and changes, and how the queues command shows
it.
"""

import json
from types import SimpleNamespace

import pika
import pytest
import dramatiq
from dramatiq.message import Message
from dramatiq.brokers import rabbitmq as dramatiq_rabbitmq
from tokeo.ext.dramatiq import (
    TokeoDramatiqError,
    TokeoDramatiqConfigError,
    TokeoDramatiqConnectionError,
    TokeoDramatiqRefusedError,
    TokeoDramatiqQueueError,
)


class FakeServer:
    """The queues of a fake rabbitmq, shared by all its connections."""

    def __init__(self):
        self.queues = {}
        self.connections = 0
        self.drop_next = False
        self.refuse_publish = False
        self.down = False
        self.declares = 0
        self.refuse = None

    def add(self, name, *bodies, consumers=0, arguments=None):
        self.queues[name] = dict(messages=list(bodies), consumers=consumers, arguments=arguments or {})


class FakeChannel:

    def __init__(self, server):
        self.server = server
        self.is_closed = False
        self.unacked = {}
        self.tag = 0

    def confirm_delivery(self):
        pass

    def queue_declare(self, queue, passive=False, durable=False, arguments=None):
        self.server.declares += 1
        if queue not in self.server.queues:
            if passive:
                self.is_closed = True
                raise pika.exceptions.ChannelClosedByBroker(404, f"NOT_FOUND - no queue '{queue}'")
            self.server.add(queue, arguments=arguments)
        q = self.server.queues[queue]
        return SimpleNamespace(method=SimpleNamespace(message_count=len(q['messages']), consumer_count=q['consumers']))

    def basic_get(self, queue, auto_ack=False):
        messages = self.server.queues[queue]['messages']
        if not messages:
            return None, None, None
        self.tag += 1
        self.unacked[self.tag] = (queue, messages.pop(0))
        return SimpleNamespace(delivery_tag=self.tag), None, self.unacked[self.tag][1]

    def basic_nack(self, delivery_tag, multiple=False, requeue=True):
        tags = sorted(t for t in self.unacked if t <= delivery_tag) if multiple else [delivery_tag]
        for tag in reversed(tags):
            queue, body = self.unacked.pop(tag)
            self.server.queues[queue]['messages'].insert(0, body)

    def basic_ack(self, delivery_tag):
        self.unacked.pop(delivery_tag)

    def basic_publish(self, exchange, routing_key, body, properties=None, mandatory=False):
        if routing_key not in self.server.queues or self.server.refuse_publish:
            raise pika.exceptions.UnroutableError([])
        self.server.queues[routing_key]['messages'].append(body)

    def queue_purge(self, queue):
        if self.server.refuse:
            self.is_closed = True
            raise pika.exceptions.ChannelClosedByBroker(*self.server.refuse)
        count = len(self.server.queues[queue]['messages'])
        self.server.queues[queue]['messages'] = []
        return SimpleNamespace(method=SimpleNamespace(message_count=count))

    def queue_delete(self, queue):
        del self.server.queues[queue]


class FakeConnection:

    def __init__(self, server):
        if server.down:
            raise pika.exceptions.AMQPConnectionError('connection refused')
        server.connections += 1
        self.server = server
        self.is_closed = False

    @property
    def is_open(self):
        return not self.is_closed

    def process_data_events(self, time_limit=0):
        if self.server.drop_next:
            # rabbitmq closed the idle connection in the meantime
            self.server.drop_next = False
            self.is_closed = True
            raise pika.exceptions.StreamLostError('connection lost')

    def channel(self):
        return FakeChannel(self.server)

    def close(self):
        self.is_closed = True


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr('tokeo.ext.dramatiq.queues.pika.BlockingConnection', lambda parameters: FakeConnection(fake))
    return fake


@pytest.fixture
def app(tmp, broker_app):
    with broker_app(tmp, None) as app:
        broker = app.dramatiq.broker
        for name, queue in (('send_mail', 'mail'), ('send_report', 'mail'), ('build_order', 'orders_unparalleled')):
            dramatiq.actor(lambda: None, actor_name=name, queue_name=queue, broker=broker)
        yield app


def dead_letter(actor='send_mail', queue='mail', args=('a@b.c',), **options):
    return Message(queue_name=queue, actor_name=actor, args=args, kwargs={}, options=options).encode()


# --- names and state -----------------------------------------------------------


def test_names_from_actors(app):
    assert app.dramatiq.queues.names() == ['mail', 'orders_unparalleled']


def test_names_import_actors_module(tmp, broker_app):
    with broker_app(tmp, None) as app:
        app.config.set('dramatiq', 'actors', 'no.such.actors')
        with pytest.raises(TokeoDramatiqConfigError, match="Invalid dramatiq config actors: Value 'no.such.actors'") as e:
            app.dramatiq.queues.names()
    assert (e.value.source, e.value.name, e.value.value) == ('config', 'actors', 'no.such.actors')


def test_state(app, server):
    server.add('mail', b'1', b'2', consumers=2)
    server.add('mail.DQ', b'3')
    server.add('mail.XQ', b'4', b'5', b'6')
    states = {s['name']: s for s in app.dramatiq.queues.state()}
    assert states['mail'] == dict(
        name='mail', exists=True, ready=2, delayed=1, dead=3, consumers=2, unparalleled=False, actors=['send_mail', 'send_report']
    )
    # a missing queue is reported, but not created
    assert states['orders_unparalleled'] == dict(
        name='orders_unparalleled',
        exists=False,
        ready=None,
        delayed=None,
        dead=None,
        consumers=None,
        unparalleled=True,
        actors=['build_order'],
    )
    assert 'orders_unparalleled' not in server.queues


def test_state_of_queue_without_actor(app, server):
    server.add('old')
    (state,) = app.dramatiq.queues.state('old.XQ')
    assert (state['name'], state['exists'], state['actors']) == ('old', True, [])


# --- dead letters --------------------------------------------------------------


def test_peek_dead_leaves_letters(app, server):
    long_args = ('x' * 600,)
    server.add('mail.XQ', dead_letter(retries=3, traceback='Traceback\n  ...\nValueError: bad'), dead_letter(args=long_args))
    letters = app.dramatiq.queues.peek_dead('mail')
    assert (letters[0]['actor'], letters[0]['retries'], letters[0]['error']) == ('send_mail', 3, 'ValueError: bad')
    assert letters[0]['args'] == "(('a@b.c',), {})"
    # the arguments are cut, the letters stay in place and in order
    assert letters[1]['args'].endswith('...') and len(letters[1]['args']) == 503
    assert [Message.decode(b).message_id for b in server.queues['mail.XQ']['messages']] == [ltr['message_id'] for ltr in letters]


def test_peek_dead_limit_and_missing(app, server):
    server.add('mail.XQ', dead_letter(), dead_letter(), dead_letter())
    assert len(app.dramatiq.queues.peek_dead('mail', limit=2)) == 2
    assert len(server.queues['mail.XQ']['messages']) == 3
    assert app.dramatiq.queues.peek_dead('orders_unparalleled') == []


def test_requeue_dead_resets_retries(app, server):
    server.add('mail')
    server.add('mail.XQ', dead_letter(queue='mail.DQ', retries=20, traceback='tb', eta=1, broker_priority=3), dead_letter())
    assert app.dramatiq.queues.requeue_dead('mail', limit=1) == dict(name='mail', requeued=1)
    (body,) = server.queues['mail']['messages']
    message = Message.decode(body)
    # retries, traceback and eta are gone, other options and the id stay
    assert (message.queue_name, message.options) == ('mail', {'broker_priority': 3})
    assert len(server.queues['mail.XQ']['messages']) == 1
    assert app.dramatiq.queues.requeue_dead('mail') == dict(name='mail', requeued=1)


def test_requeue_dead_refuses_missing_queue(app, server):
    server.add('mail.XQ', dead_letter())
    with pytest.raises(TokeoDramatiqQueueError, match='Queue mail is missing') as e:
        app.dramatiq.queues.requeue_dead('mail')
    assert e.value.queue == 'mail'
    assert len(server.queues['mail.XQ']['messages']) == 1


def test_requeue_dead_keeps_letter_when_publish_fails(app, server):
    # a refused message goes back to the dead letters at once
    server.add('mail')
    server.add('mail.XQ', dead_letter())
    server.refuse_publish = True
    with pytest.raises(TokeoDramatiqRefusedError, match='refused the request \\(UnroutableError'):
        app.dramatiq.queues.requeue_dead('mail')
    assert len(server.queues['mail.XQ']['messages']) == 1


# --- maintenance ---------------------------------------------------------------


def test_purge(app, server):
    for name in ('mail', 'mail.DQ', 'mail.XQ'):
        server.add(name, b'1', b'2')
    assert app.dramatiq.queues.purge('mail', dead_only=True) == [dict(name='mail', purged=2)]
    assert len(server.queues['mail']['messages']) == 2
    # all queues of the app; missing ones count nothing
    assert app.dramatiq.queues.purge() == [dict(name='mail', purged=4), dict(name='orders_unparalleled', purged=0)]
    assert all(not server.queues[name]['messages'] for name in ('mail', 'mail.DQ', 'mail.XQ'))


@pytest.mark.parametrize(
    'setup, busy',
    [
        (dict(name='mail', bodies=[b'1']), 'ready'),
        (dict(name='mail.XQ', bodies=[b'1']), 'dead'),
        (dict(name='mail', bodies=[], consumers=1), 'consumers'),
    ],
)
def test_delete_refuses_busy_queue(app, server, setup, busy):
    server.add(setup['name'], *setup['bodies'], consumers=setup.get('consumers', 0))
    with pytest.raises(TokeoDramatiqQueueError, match=f'Queue mail has {busy}, use force'):
        app.dramatiq.queues.delete('mail')
    assert setup['name'] in server.queues


def test_delete(app, server):
    for name in ('mail', 'mail.DQ', 'mail.XQ'):
        server.add(name, b'1', consumers=1)
    assert app.dramatiq.queues.delete(['mail', 'old'], force=True) == [dict(name='mail', deleted=True), dict(name='old', deleted=False)]
    assert server.queues == {}


def test_recreate(app, server, monkeypatch):
    monkeypatch.setattr(dramatiq_rabbitmq, 'DEAD_MESSAGE_TTL', 60000)
    monkeypatch.setattr(app.dramatiq.broker, 'max_priority', 5)
    server.add('orders_unparalleled', arguments={'old': True})
    (result,) = app.dramatiq.queues.recreate('orders_unparalleled')
    arguments = server.queues['orders_unparalleled']['arguments']
    assert result == dict(name='orders_unparalleled', arguments=arguments)
    assert (arguments['x-max-priority'], arguments['x-single-active-consumer']) == (5, True)
    assert server.queues['orders_unparalleled.DQ']['arguments'] == arguments
    assert server.queues['orders_unparalleled.XQ']['arguments'] == {'x-message-ttl': 60000}


def test_recreate_refuses_queue_without_actor(app, server):
    with pytest.raises(TokeoDramatiqQueueError, match='Queue old has no actor'):
        app.dramatiq.queues.recreate(['mail', 'old'])


# --- connection ----------------------------------------------------------------


@pytest.mark.parametrize(
    'url, address',
    [
        ('amqp://user:secret@mq.example:5673/', 'mq.example:5673'),
        ('amqps://user:secret@mq.example/', 'mq.example:5671'),
    ],
)
def test_error_without_rabbitmq(tmp, broker_app, server, url, address):
    server.down = True
    with broker_app(tmp, dict(url=url)) as app:
        dramatiq.actor(lambda: None, actor_name='send_mail', queue_name='mail', broker=app.dramatiq.broker)
        with pytest.raises(TokeoDramatiqConnectionError, match=rf'Cannot connect to rabbitmq at {address} \(AMQPConnectionError: ') as e:
            app.dramatiq.queues.state('mail')
    # the error names host and port, never the password of the url
    assert 'secret' not in str(e.value)
    assert (e.value.address, e.value.actor_name) == (address, None)
    assert e.value.cause.startswith('AMQPConnectionError: ')
    assert isinstance(e.value.__cause__, pika.exceptions.AMQPConnectionError)


def test_connection_is_kept_and_reopened(app, server):
    queues = app.dramatiq.queues
    queues.state()
    queues.state()
    assert server.connections == 1
    # a connection closed by rabbitmq gets opened again on the next call
    server.drop_next = True
    queues.state()
    assert server.connections == 2
    queues.close()
    assert queues._connection is None


# --- queues command ------------------------------------------------------------


@pytest.fixture
def command(tmp, dramatiq_app, server):
    def run(*options):
        with dramatiq_app(tmp, ['dramatiq', 'queues', *options]) as app:
            dramatiq.actor(lambda: None, actor_name='send_mail', queue_name='mail', broker=app.dramatiq.broker)
            app.run()
            # cement renders to the stdout it bound on import, so read the text
            return app.last_rendered[1]

    return run


def test_command_state_table(command, server):
    server.add('mail', b'1', consumers=0)
    server.add('mail.XQ', dead_letter())
    lines = command('mail', '--dead').splitlines()
    assert lines[0].split() == ['QUEUE', 'READY', 'DELAYED', 'DEAD', 'CONSUMERS', 'FLAGS']
    assert lines[1].split() == ['mail', '1', '-', '1', '0', 'no', 'consumer']
    assert 'dead letters of mail:' in lines
    assert lines[-1].strip() == "args: (('a@b.c',), {})"


def test_command_json(command, server):
    server.add('mail', b'1')
    (state,) = json.loads(command('--json'))
    assert (state['name'], state['ready']) == ('mail', 1)


def test_command_purge(command, server):
    server.add('mail', b'1', b'2')
    assert command('mail', '--purge', '--yes').strip() == 'mail: purged 2 messages'


@pytest.mark.parametrize(
    'options, error',
    [
        (['--purge'], 'confirm it by --yes'),
        (['--purge', '--delete', '--yes'], 'Use only one of --purge, --delete'),
        (['--dead-only', '--yes'], 'Use --dead-only together with --purge'),
        (['--requeue-dead', '--yes'], 'Name the queues for --dead or --requeue-dead'),
        (['mail', '--dead', '0'], 'Invalid switch --dead'),
    ],
)
def test_command_refuses(command, options, error):
    with pytest.raises(TokeoDramatiqError, match=error):
        command(*options)


# --- cache ---------------------------------------------------------------------


def test_state_without_cache_asks_each_time(app, server):
    app.dramatiq.queues.state('mail')
    app.dramatiq.queues.state('mail')
    assert server.declares == 6


def test_state_cache(app, server, monkeypatch):
    now = [100.0]
    monkeypatch.setattr('tokeo.ext.dramatiq.queues.time.monotonic', lambda: now[0])
    server.add('mail', b'1')
    queues = app.dramatiq.queues
    first = queues.state('mail', cache_ttl=5)
    server.add('mail', b'1', b'2')
    now[0] += 4
    # within the ttl the kept state comes back, rabbitmq is not asked
    assert queues.state('mail', cache_ttl=5) == first and server.declares == 3
    # a caller may change its copy without changing the cache
    first[0]['ready'] = 99
    assert queues.state('mail', cache_ttl=5)[0]['ready'] == 1
    now[0] += 1
    assert queues.state('mail', cache_ttl=5)[0]['ready'] == 2


def test_state_cache_per_names_and_cleared_by_changes(app, server):
    server.add('mail', b'1')
    queues = app.dramatiq.queues
    queues.state('mail', cache_ttl=60)
    queues.state(cache_ttl=60)
    assert server.declares == 9
    queues.purge('mail')
    assert queues.state('mail', cache_ttl=60)[0]['ready'] == 0


def test_refused_request(app, server):
    server.add('mail', b'1')
    server.refuse = (403, "ACCESS_REFUSED - access to queue 'mail' refused")
    refused = r'Rabbitmq at localhost:5672 refused the request \(ChannelClosedByBroker: .*ACCESS_REFUSED'
    with pytest.raises(TokeoDramatiqRefusedError, match=refused) as e:
        app.dramatiq.queues.purge('mail')
    assert e.value.address == 'localhost:5672' and e.value.__cause__.reply_code == 403
    # the next call works on a new channel of the same connection
    server.refuse = None
    assert app.dramatiq.queues.purge('mail') == [dict(name='mail', purged=1)]
    assert server.connections == 1


def test_refused_request_is_no_connection_error(app, server):
    server.add('mail')
    server.refuse = (406, 'PRECONDITION_FAILED - inequivalent arg')
    with pytest.raises(TokeoDramatiqError) as e:
        app.dramatiq.queues.purge('mail')
    assert not isinstance(e.value, TokeoDramatiqConnectionError)
