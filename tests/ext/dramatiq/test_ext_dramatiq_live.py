"""
Tests for tokeo.ext.dramatiq against a live rabbitmq.

They run only when a rabbitmq answers at ```TOKEO_TEST_RABBITMQ_URL```
(default ```amqp://guest:guest@localhost:5672/```), else they are skipped. Each
test works on queues with a random name and deletes them afterwards.
"""

import os
import time
import socket
from contextlib import contextmanager

import pika
import pytest
import dramatiq
from dramatiq.message import Message
from tokeo.ext.dramatiq import TokeoDramatiqError
from tests.utils import wait_until
from tests.ext.dramatiq.lib.dramatiq_tests_helper import DramatiqBrokerTest, DramatiqMiddlewareTest

RABBITMQ_URL = os.environ.get('TOKEO_TEST_RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/')


def _reachable(url):
    parameters = pika.URLParameters(url)
    try:
        socket.create_connection((parameters.host, parameters.port), timeout=1).close()
        return True
    except OSError:
        return False


needs_rabbitmq = pytest.mark.skipif(
    not _reachable(RABBITMQ_URL),
    reason=f'needs a rabbitmq at {pika.URLParameters(RABBITMQ_URL).host}:{pika.URLParameters(RABBITMQ_URL).port} '
    '(set TOKEO_TEST_RABBITMQ_URL)',
)

pytestmark = needs_rabbitmq


@contextmanager
def raw_channel():
    """A channel of its own, to put and check messages beside the app."""
    connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
    try:
        yield connection.channel()
    finally:
        connection.close()


@pytest.fixture
def live(tmp, rando):
    """An app on the live rabbitmq with one actor on a queue of its own."""

    @contextmanager
    def build(url=RABBITMQ_URL, **rabbitmq):
        with DramatiqBrokerTest(tmp, dict(url=url, **rabbitmq)) as app:
            name = f'tokeo_test_{rando}'
            actor = dramatiq.actor(lambda value: None, actor_name=f'task_{rando}', queue_name=name, broker=app.dramatiq.broker)
            try:
                yield app, name, actor
            finally:
                app.dramatiq.queues.delete(name, force=True)

    return build


def test_state(live):
    """state reports a missing queue, then 2 ready and 1 delayed message."""
    with live() as (app, name, actor):
        queues = app.dramatiq.queues
        # a missing queue is reported and not created by the state
        (state,) = queues.state()
        assert (state['name'], state['exists']) == (name, False)
        assert queues.state()[0]['exists'] is False
        actor.send(1)
        actor.send(2)
        actor.send_with_options(args=(3,), delay=600_000)
        (state,) = queues.state()
        assert (state['exists'], state['ready'], state['delayed'], state['dead'], state['consumers']) == (True, 2, 1, 0, 0)


def test_dead_letters(live):
    """A dead letter is peeked and requeued with its retries reset."""
    with live() as (app, name, actor):
        queues = app.dramatiq.queues
        actor.send(1)
        letter = Message(queue_name=name, actor_name=actor.actor_name, args=(7,), kwargs={}, options=dict(retries=3, traceback='E: x'))
        with raw_channel() as channel:
            channel.basic_publish(exchange='', routing_key=f'{name}.XQ', body=letter.encode())
        (peeked,) = queues.peek_dead(name)
        assert (peeked['message_id'], peeked['retries'], peeked['error']) == (letter.message_id, 3, 'E: x')
        assert queues.state(name)[0]['dead'] == 1
        assert queues.requeue_dead(name) == dict(name=name, requeued=1)
        assert (queues.state(name)[0]['ready'], queues.state(name)[0]['dead']) == (2, 0)
        with raw_channel() as channel:
            bodies = [channel.basic_get(name, auto_ack=True)[2] for _ in range(2)]
        requeued = [Message.decode(b) for b in bodies if Message.decode(b).message_id == letter.message_id][0]
        # the requeued message starts its retries again
        assert 'retries' not in requeued.options and 'traceback' not in requeued.options


def test_purge_and_delete(live):
    """delete needs force on a busy queue, purge empties it, delete removes it."""
    with live() as (app, name, actor):
        queues = app.dramatiq.queues
        actor.send(1)
        actor.send_with_options(args=(2,), delay=600_000)
        with pytest.raises(TokeoDramatiqError, match='use force'):
            queues.delete(name)
        assert queues.purge(name) == [dict(name=name, purged=2)]
        assert queues.delete(name) == [dict(name=name, deleted=True)]
        assert queues.state(name)[0]['exists'] is False


def test_recreate_with_priority(live):
    """recreate sets x-max-priority, rabbitmq refuses other arguments."""
    with live(max_priority=5) as (app, name, actor):
        actor.send(1)
        (result,) = app.dramatiq.queues.recreate(name, force=True)
        assert result['arguments']['x-max-priority'] == 5
        # rabbitmq takes the same arguments again, but refuses other ones
        with raw_channel() as channel:
            channel.queue_declare(name, durable=True, arguments=result['arguments'])
            with pytest.raises(pika.exceptions.ChannelClosedByBroker, match='PRECONDITION_FAILED'):
                channel.queue_declare(name, durable=True, arguments=dict(result['arguments'], **{'x-max-priority': 3}))


def test_reconnect_after_idle(live):
    """After rabbitmq closed the idle connection, the next call reconnects."""
    with live(url=RABBITMQ_URL + ('&' if '?' in RABBITMQ_URL else '?') + 'heartbeat=1') as (app, name, actor):
        queues = app.dramatiq.queues
        queues.state(name)
        first = queues._connection
        time.sleep(3.5)
        assert queues.state(name)[0]['name'] == name
        assert queues._connection is not first


def test_monitor_hears_a_worker(tmp, rando):
    """The monitor sees a worker running a task and then stopped with 1 done."""
    config = dict(monitoring=True, monitoring_interval=1, monitoring_with_details='on', rabbitmq=dict(url=RABBITMQ_URL))
    with DramatiqMiddlewareTest(tmp, **config) as app:
        name = f'tokeo_test_{rando}'
        actor = dramatiq.actor(
            lambda seconds: time.sleep(seconds), actor_name=f'task_{rando}', queue_name=name, broker=app.dramatiq.broker
        )
        monitor = app.dramatiq.monitor
        worker = dramatiq.Worker(app.dramatiq.broker, worker_threads=1, worker_timeout=100, queues={name})

        def mine(wait):
            return [w for w in monitor.workers(wait) if w['heartbeat']['worker']['pid'] == os.getpid()]

        try:
            worker.start()
            actor.send(2)
            assert wait_until(lambda: mine(0.5) and mine(0)[0]['heartbeat']['running'], 15)
            (running,) = mine(0)
            worker.stop()
            (stopped,) = mine(1)
        finally:
            worker.stop()
            app.dramatiq.queues.delete(name, force=True)
    (task,) = running['heartbeat']['running']
    assert running['state'] == 'ok' and task['actor'] == f'task_{rando}' and task['args'] == '((2,), {})'
    assert stopped['state'] == 'stopped' and stopped['heartbeat']['totals']['done'] == 1


def test_unparalleled_runs_one_message_at_a_time(tmp, rando):
    """An unparalleled queue runs its 4 messages one after another."""
    with DramatiqBrokerTest(tmp, dict(url=RABBITMQ_URL)) as app:
        name = f'tokeo_test_{rando}_unparalleled'
        runs = []

        def task(value):
            start = time.monotonic()
            time.sleep(0.3)
            runs.append((start, time.monotonic()))

        actor = dramatiq.actor(task, actor_name=f'task_{rando}', queue_name=name, broker=app.dramatiq.broker)
        # more threads than one, they must not run the queue in parallel
        worker = dramatiq.Worker(app.dramatiq.broker, worker_threads=3, worker_timeout=100, queues={name})
        try:
            worker.start()
            for value in range(4):
                actor.send(value)
            assert wait_until(lambda: len(runs) >= 4, 20)
        finally:
            worker.stop()
            app.dramatiq.queues.delete(name, force=True)
    runs.sort()
    assert len(runs) == 4
    assert all(previous[1] <= following[0] for previous, following in zip(runs, runs[1:]))
