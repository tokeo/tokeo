"""
Tests for tokeo.ext.dramatiq on the worker side, without rabbitmq: register
builds dramatiq's stub broker in place of the rabbitmq broker, with the
middleware of the config, and an in-process dramatiq worker runs the actors.

They cover what the other tests leave to a live server: actors sent and run,
the retries, time limit, age limit and shutdown notice of the config, the
asyncio, current message, callbacks and pipelines middleware, the diskcache
locks of ```app.dramatiq.locks``` around actors and the heartbeats of a worker
reaching the monitor. Queue state, dead letters in rabbitmq, the reconnect,
the heartbeat transport and the single active consumer still need the live
tests.
"""

import json
import time
import threading
import dataclasses

import pytest
import dramatiq
from dramatiq import broker as dramatiq_broker
from dramatiq import pipeline
from dramatiq.asyncio import get_event_loop_thread
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import CurrentMessage, Shutdown, TimeLimitExceeded
from tokeo.ext.dramatiq import TokeoActor
from tokeo.ext.dramatiq.monitoring import TokeoDramatiqHeartbeat
from tests.utils import wait_until
from tests.ext.dramatiq.lib.dramatiq_tests_helper import DramatiqTest, DramatiqMiddlewareTest


class _RabbitmqStub(StubBroker):
    # takes the place of ExtendedRabbitmqBroker in register: keeps the
    # middleware built from the config, drops the rabbitmq settings
    def __init__(self, *, middleware=None, **rabbitmq):
        super().__init__(middleware=middleware)


@pytest.fixture(autouse=True)
def keep_global_broker(monkeypatch):
    """The global broker of dramatiq, restored after the test, register sets it."""
    monkeypatch.setattr(dramatiq_broker, 'global_broker', dramatiq_broker.global_broker)


@pytest.fixture(autouse=True)
def stub_broker(monkeypatch):
    """register builds the stub broker instead of the rabbitmq broker."""
    monkeypatch.setattr('tokeo.ext.dramatiq.base.ExtendedRabbitmqBroker', _RabbitmqStub)


@pytest.fixture
def start_worker():
    """Starts in-process workers and stops those a test left running."""
    workers = []

    def start(broker, threads=1):
        worker = dramatiq.Worker(broker, worker_threads=threads, worker_timeout=20)
        worker.start()
        workers.append(worker)
        return worker

    yield start
    for worker in workers:
        # a second stop would shut the event loop thread down twice
        if any(thread.is_alive() for thread in worker.workers):
            worker.stop(timeout=5000)


def _join(broker, queue='default'):
    # wait until the queue and its delay queue are done, retries included
    broker.join(queue, fail_fast=False, timeout=10_000)


def _dead_ids(broker):
    # the ids of the messages the stub broker took as dead letters
    return [message.message_id for message in broker.dead_letters]


# ---- actors -------------------------------------------------------------------


def test_actor_sent_runs_in_the_worker(tmp, start_worker):
    """A TokeoActor sent with 21 runs once in the worker and doubles it."""
    with DramatiqTest(tmp) as app:
        results = []
        actor = app.dramatiq.actor(lambda value: results.append(value * 2), actor_name='double', queue_name='mail')
        assert isinstance(app.dramatiq.broker, StubBroker) and type(actor) is TokeoActor
        start_worker(app.dramatiq.broker)
        actor.send(21)
        _join(app.dramatiq.broker, 'mail')
        assert results == [42] and app.dramatiq.broker.dead_letters == []


def test_async_actor_runs_on_the_daemon_event_loop(tmp, start_worker):
    """An async actor runs on the daemon loop thread, which ends with the worker."""
    with DramatiqTest(tmp) as app:
        threads = []

        async def remember():
            threads.append(threading.current_thread())

        actor = app.dramatiq.actor(remember, actor_name='remember')
        worker = start_worker(app.dramatiq.broker)
        actor.send()
        _join(app.dramatiq.broker)
        worker.stop(timeout=5000)
    (thread,) = threads
    assert thread is not threading.main_thread() and thread.daemon is True
    assert get_event_loop_thread() is None and not thread.is_alive()


def test_current_message_is_the_one_sent(tmp, start_worker):
    """The actor sees the message it runs by CurrentMessage."""
    with DramatiqTest(tmp) as app:
        seen = []
        actor = app.dramatiq.actor(lambda: seen.append(CurrentMessage.get_current_message().message_id), actor_name='current')
        start_worker(app.dramatiq.broker)
        message = actor.send()
        _join(app.dramatiq.broker)
    assert seen == [message.message_id]


def test_pipeline_hands_each_result_on(tmp, start_worker):
    """A pipeline of double(2), double and keep ends with 8 in keep."""
    with DramatiqTest(tmp) as app:
        kept = []
        double = app.dramatiq.actor(lambda value: value * 2, actor_name='double')
        keep = app.dramatiq.actor(kept.append, actor_name='keep')
        start_worker(app.dramatiq.broker)
        pipeline([double.message(2), double.message(), keep.message()]).run()
        assert wait_until(lambda: kept == [8])


def test_callbacks_get_result_and_error(tmp, start_worker):
    """on_success gets the result 42, on_failure the type of the error."""
    with DramatiqTest(tmp, max_retries=0) as app:
        calls = []
        app.dramatiq.actor(lambda message, result: calls.append(('success', result)), actor_name='succeeded')
        app.dramatiq.actor(lambda message, error: calls.append(('failure', error['type'])), actor_name='failed')

        def answer(fail):
            if fail:
                raise LookupError('no answer')
            return 42

        actor = app.dramatiq.actor(answer, actor_name='answer', on_success='succeeded', on_failure='failed')
        start_worker(app.dramatiq.broker)
        actor.send(False)
        actor.send(True)
        assert wait_until(lambda: len(calls) == 2)
    assert sorted(calls) == [('failure', 'LookupError'), ('success', 42)]


# ---- retries ------------------------------------------------------------------


@pytest.mark.parametrize(
    'actor_options, message_options, runs',
    [
        ({}, {}, 3),
        (dict(max_retries=0), {}, 1),
        (dict(max_retries=0), dict(max_retries=1), 2),
    ],
    ids=['config', 'actor', 'message'],
)
def test_retries_follow_message_actor_and_config(tmp, start_worker, actor_options, message_options, runs):
    """max_retries of the message wins over the actor, the actor over the config."""
    with DramatiqMiddlewareTest(tmp, max_retries=2, min_backoff=10, max_backoff=20) as app:
        calls = []

        def fails():
            calls.append(time.monotonic())
            raise LookupError('not found')

        actor = app.dramatiq.actor(fails, actor_name='fails', **actor_options)
        start_worker(app.dramatiq.broker)
        message = actor.send_with_options(**message_options)
        _join(app.dramatiq.broker)
        # after the last retry the message is a dead letter
        assert len(calls) == runs and _dead_ids(app.dramatiq.broker) == [message.message_id]


# ---- time limit, age limit and shutdown notice -------------------------------


def test_time_limit_from_config_stops_a_long_actor(tmp, start_worker):
    """time_limit 100 ms of the config raises TimeLimitExceeded in the actor."""
    with DramatiqMiddlewareTest(tmp, time_limit=100, max_retries=0) as app:
        raised = []

        def endless():
            try:
                while True:
                    time.sleep(0.01)
            except TimeLimitExceeded as e:
                raised.append(e)
                raise

        actor = app.dramatiq.actor(endless, actor_name='endless')
        # the dramatiq cli boots each worker process so, the limit checks start here
        app.dramatiq.broker.emit_after('process_boot')
        start_worker(app.dramatiq.broker)
        message = actor.send()
        _join(app.dramatiq.broker)
        # the limit is checked once a second
        assert len(raised) == 1 and _dead_ids(app.dramatiq.broker) == [message.message_id]


def test_max_age_from_config_skips_old_messages(tmp, start_worker):
    """With max_age 60 s a message 2 minutes old is dead, a new one runs."""
    with DramatiqMiddlewareTest(tmp, max_age=60_000) as app:
        runs = []
        actor = app.dramatiq.actor(runs.append, actor_name='keep')
        broker = app.dramatiq.broker
        start_worker(broker)
        new = actor.message('new')
        old = dataclasses.replace(actor.message('old'), message_timestamp=new.message_timestamp - 120_000)
        broker.enqueue(old)
        broker.enqueue(new)
        _join(broker)
        assert runs == ['new'] and _dead_ids(broker) == [old.message_id]


@pytest.mark.parametrize('notify_shutdown, ended', [(True, 'Shutdown'), (False, 'finished')])
def test_notify_shutdown_from_config(tmp, start_worker, notify_shutdown, ended):
    """notify_shutdown of the config raises Shutdown on stop, else it finishes."""
    with DramatiqMiddlewareTest(tmp, notify_shutdown=notify_shutdown, max_retries=0) as app:
        started, results = threading.Event(), []

        def busy():
            started.set()
            try:
                # a little longer than the stop takes to raise the notice
                for _ in range(30):
                    time.sleep(0.01)
            except Shutdown:
                results.append('Shutdown')
                raise
            results.append('finished')

        actor = app.dramatiq.actor(busy, actor_name='busy')
        worker = start_worker(app.dramatiq.broker)
        actor.send()
        assert started.wait(5)
        worker.stop(timeout=5000)
    assert results == [ended]


# ---- locks --------------------------------------------------------------------


def test_locks_temper_keeps_worker_threads_apart(tmp, start_worker):
    """A temper of 1 runs 4 messages on 3 threads one after another."""
    with DramatiqMiddlewareTest(tmp, locks_sleep_time=0.01) as app:
        runs = []

        @app.dramatiq.actor(actor_name='export')
        @app.dramatiq.locks.temper(count=1, name='export', verbose=False)
        def export(value):
            start = time.monotonic()
            time.sleep(0.05)
            runs.append((start, time.monotonic()))

        start_worker(app.dramatiq.broker, threads=3)
        for value in range(4):
            export.send(value)
        _join(app.dramatiq.broker)
    runs.sort()
    assert len(runs) == 4
    assert all(previous[1] <= following[0] for previous, following in zip(runs, runs[1:]))


def test_locks_throttle_hands_surplus_messages_to_the_callback(tmp, start_worker):
    """A throttle of 2 per hour runs 2 of 5 messages, the callback gets 3."""
    with DramatiqMiddlewareTest(tmp) as app:
        ran, locked = [], []

        @app.dramatiq.actor(actor_name='call_api')
        @app.dramatiq.locks.throttle(
            count=2, per_seconds=3600, name='api', cb_on_locked=lambda **kw: locked.append(kw['value']), verbose=False
        )
        def call_api(value):
            ran.append(value)

        start_worker(app.dramatiq.broker)
        for value in range(5):
            call_api.send(value)
        _join(app.dramatiq.broker)
    assert (len(ran), len(locked), sorted(ran + locked)) == (2, 3, [0, 1, 2, 3, 4])


def test_locks_purge_command_frees_a_lock_an_actor_waits_for(tmp, start_worker, capsys):
    """dramatiq locks --purge drops a lock left behind, the waiting actor runs."""
    with DramatiqMiddlewareTest(tmp, ['dramatiq', 'locks', '--purge'], locks_sleep_time=0.01) as app:
        ran = threading.Event()

        def report():
            with app.dramatiq.locks.lock('report'):
                ran.set()

        actor = app.dramatiq.actor(report, actor_name='report')
        # a lock of a crashed worker, it would hold for an hour
        app.dramatiq.locks.acquire('report')
        start_worker(app.dramatiq.broker)
        actor.send()
        # the actor keeps waiting while the lock is held
        assert not ran.wait(0.3)
        app.run()
        assert ran.wait(5)
    assert capsys.readouterr().out.strip() == 'Purged 1 dramatiq locks from cache.'


# ---- monitoring ---------------------------------------------------------------


def test_monitor_hears_a_worker_of_the_app(tmp, start_worker, monkeypatch):
    """
    The heartbeat of the app config, handed to the monitor as json instead of
    by rabbitmq, shows the worker running a task with its args and then
    stopped with 1 task done.
    """
    config = dict(monitoring=True, monitoring_interval=1, monitoring_with_details='on')
    with DramatiqMiddlewareTest(tmp, **config) as app:
        monitor = app.dramatiq.monitor
        (heartbeat,) = [m for m in app.dramatiq.broker.middleware if isinstance(m, TokeoDramatiqHeartbeat)]
        # the heartbeats reach the monitor directly, it listens to no rabbitmq
        monkeypatch.setattr(heartbeat, '_send', lambda beat: monitor.receive(json.dumps(beat)))
        monkeypatch.setattr(monitor, '_collect', lambda wait: None)
        release = threading.Event()
        actor = app.dramatiq.actor(lambda value: release.wait(10), actor_name='waits')
        worker = start_worker(app.dramatiq.broker)
        try:
            actor.send(2)
            assert wait_until(lambda: monitor.workers() and monitor.workers()[0]['heartbeat']['running'])
            (running,) = monitor.workers()
        finally:
            release.set()
        _join(app.dramatiq.broker)
        worker.stop(timeout=5000)
        (stopped,) = monitor.workers()
    (task,) = running['heartbeat']['running']
    assert (running['state'], task['actor'], task['args']) == ('ok', 'waits', '((2,), {})')
    assert stopped['state'] == 'stopped' and stopped['heartbeat']['totals'] == dict(done=1, failed=0, dead=0)
