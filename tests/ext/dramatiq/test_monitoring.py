"""
Tests for tokeo.ext.dramatiq.monitoring and the monitor command.

The heartbeats of the middleware are recorded instead of sent, the monitor
gets them by ```receive```; the way through rabbitmq is in the live tests.
"""

import sys
import json
import time
import threading

import pika
import pytest
import dramatiq
from dramatiq import broker as dramatiq_broker
from dramatiq.brokers.stub import StubBroker
from tokeo.ext.dramatiq import TokeoDramatiqConfigError, TokeoDramatiqConnectionError
from tokeo.ext.dramatiq import monitoring
from tokeo.ext.dramatiq.base import _get_with_details
from tokeo.ext.dramatiq.monitoring import TokeoDramatiqHeartbeat
from tokeo.ext.dramatiq.controller import _format_duration, _format_monitor_view
from tokeo.ext.tui.output import TokeoTuiOutput
from tokeo.core.exc import TokeoError
from tests.utils import wait_until
from tests.utils.tui import Terminal
from tests.ext.dramatiq.lib.dramatiq_tests_helper import DramatiqTest, DramatiqMiddlewareTest

needs_pty = pytest.mark.skipif(sys.platform == 'win32', reason='needs a pseudo terminal')


@pytest.fixture(autouse=True)
def keep_global_broker(monkeypatch):
    """The global broker of dramatiq, restored after the test, register sets it."""
    monkeypatch.setattr(dramatiq_broker, 'global_broker', dramatiq_broker.global_broker)


class DramatiqWithoutTuiTest(DramatiqTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.diskcache',
            'tokeo.ext.dramatiq',
        ]


# ---- config -----------------------------------------------------------------


@pytest.mark.parametrize('value', [True, 'on', 'yes', 'True', 'ON'])
def test_details_on(value):
    """True, "on", "yes" in any case switch the details on."""
    assert _get_with_details(value, debug=False) is True


@pytest.mark.parametrize('value', [False, None, 'off', 'no', 'null', 'False'])
def test_details_off(value):
    """False, None, "off", "no", "null" switch the details off."""
    assert _get_with_details(value, debug=True) is False


@pytest.mark.parametrize('debug', [True, False])
def test_details_follow_debug(debug):
    """ "debug" switches the details on in debug mode only."""
    assert _get_with_details('debug', debug) is debug


@pytest.mark.parametrize('value', ['maybe', 1])
def test_details_invalid(value):
    """Any other value raises the config error."""
    with pytest.raises(TokeoDramatiqConfigError, match='Invalid dramatiq config monitoring_with_details: .* must be off, on or debug!'):
        _get_with_details(value, debug=False)


def _get_heartbeats(app):
    return [m for m in app.dramatiq.broker.middleware if isinstance(m, TokeoDramatiqHeartbeat)]


def test_monitoring_off_adds_no_heartbeat(tmp):
    """Without monitoring there is no heartbeat, the interval stays 5."""
    with DramatiqMiddlewareTest(tmp) as app:
        assert _get_heartbeats(app) == []
        assert app.dramatiq.config('monitoring') is False and app.dramatiq.monitor.interval_seconds == 5


def test_monitoring_adds_the_heartbeat(tmp):
    """monitoring adds the heartbeat with exchange, interval and details."""
    with DramatiqMiddlewareTest(tmp, monitoring=True, monitoring_interval_seconds=7, monitoring_with_details='on') as app:
        (heartbeat,) = _get_heartbeats(app)
        assert (heartbeat.exchange, heartbeat.interval_seconds, heartbeat.with_details) == ('tokeo_test.dramatiq.monitoring', 7, True)
        assert heartbeat.app_dict['label'] == 'tokeo_test'


@pytest.mark.parametrize(
    'config, error',
    [
        (dict(monitoring='yes'), 'Invalid dramatiq config monitoring: Value .* must be true or false!'),
        (dict(monitoring_interval_seconds=0), 'Invalid dramatiq config monitoring_interval_seconds'),
        (dict(monitoring_with_details='maybe'), 'Invalid dramatiq config monitoring_with_details'),
    ],
)
def test_monitoring_config_checked(tmp, config, error):
    """Invalid monitoring options raise the config error."""
    with pytest.raises(TokeoDramatiqConfigError, match=error):
        with DramatiqMiddlewareTest(tmp, **config):
            pass


# ---- heartbeat middleware ---------------------------------------------------


@pytest.fixture
def worker():
    """Builds a stub broker with the heartbeat middleware and its heartbeats."""

    def build(with_details=False, interval_seconds=60):
        broker = StubBroker()
        heartbeat = TokeoDramatiqHeartbeat('probe.dramatiq.monitoring', interval_seconds, dict(label='probe', version='1.0'), with_details)
        sent = []
        heartbeat._send = sent.append
        broker.add_middleware(heartbeat)
        release = threading.Event()

        @dramatiq.actor(broker=broker, max_retries=0)
        def ok(value):
            pass

        @dramatiq.actor(broker=broker, max_retries=0)
        def fails(email):
            raise LookupError(f'user {email} not found')

        @dramatiq.actor(broker=broker, max_retries=0)
        def waits(email):
            release.wait(10)

        worker = dramatiq.Worker(broker, worker_threads=2, worker_timeout=50)
        return broker, worker, heartbeat, sent, release, dict(ok=ok, fails=fails, waits=waits)

    return build


def _join(broker):
    broker.join('default', fail_fast=False)


def test_heartbeat_counts_tasks(worker):
    """The heartbeat counts done, failed and dead tasks, per actor too."""
    broker, w, heartbeat, sent, release, actors = worker()
    w.start()
    try:
        actors['ok'].send(1)
        actors['ok'].send(2)
        actors['fails'].send('anna@example.com')
        _join(broker)
        w.join()
        beat = heartbeat.build_heartbeat()
    finally:
        w.stop()
    assert sent[0]['state'] == 'running'
    assert beat['totals'] == dict(done=2, failed=1, dead=1)
    assert beat['since_last']['actors']['ok']['done'] == 2
    # without details only the type of the error, it never holds data
    fails = beat['since_last']['actors']['fails']
    assert (fails['done'], fails['failed'], fails['last_error']) == (0, 1, 'LookupError')
    # the next heartbeat starts its summary anew
    assert heartbeat.build_heartbeat()['since_last']['actors'] == {}


def test_heartbeat_describes_the_worker(worker):
    """The heartbeat describes app and worker and is sent on stop too."""
    broker, w, heartbeat, sent, release, actors = worker()
    w.start()
    try:
        beat = heartbeat.build_heartbeat()
    finally:
        w.stop()
    assert (beat['type'], beat['version'], beat['interval_seconds']) == ('tokeo.dramatiq.heartbeat', 1, 60)
    assert beat['app'] == dict(label='probe', version='1.0')
    assert beat['worker']['threads'] == 2 and beat['worker']['queues'] is None and beat['worker']['with_details'] is False
    assert beat['worker']['queue_prefetch'] == w.queue_prefetch and beat['worker']['started_at'] <= beat['sent_at']
    # a last heartbeat on shutdown
    assert sent[-1]['state'] == 'stopped'


@pytest.mark.parametrize('with_details', [False, True])
def test_heartbeat_shows_running_tasks(worker, with_details):
    """A running task shows actor, queue and runtime, args with details."""
    broker, w, heartbeat, sent, release, actors = worker(with_details=with_details)
    w.start()
    try:
        actors['waits'].send('anna@example.com')
        assert wait_until(lambda: heartbeat.build_heartbeat()['running'], 5)
        (task,) = heartbeat.build_heartbeat()['running']
        release.set()
        _join(broker)
        w.join()
    finally:
        release.set()
        w.stop()
    assert (task['actor_name'], task['queue_name'], task['retries']) == ('waits', 'default', 0)
    assert task['runtime_ms'] >= 0 and 'start_time' not in task
    assert ('args' in task) is with_details
    if with_details:
        assert task['args'] == "(('anna@example.com',), {})"
    assert heartbeat.build_heartbeat()['running'] == []


def test_heartbeat_details_show_error_texts(worker):
    """With details the last error shows its text."""
    broker, w, heartbeat, sent, release, actors = worker(with_details=True)
    w.start()
    try:
        actors['fails'].send('anna@example.com')
        _join(broker)
        w.join()
    finally:
        w.stop()
    assert sent[-1]['since_last']['actors']['fails']['last_error'] == 'LookupError: user anna@example.com not found'


def test_format_details_cuts_long_details():
    """Details over 500 chars are cut by "..."."""
    assert monitoring.format_details('x' * 600) == 'x' * 500 + '...'


class FakeChannel:

    def __init__(self):
        self.declared, self.published = [], []

    def exchange_declare(self, exchange, exchange_type):
        self.declared.append((exchange, exchange_type))

    def basic_publish(self, **publish):
        self.published.append(publish)


class FakeConnection:

    def __init__(self, parameters):
        self.is_closed, self.is_open = False, True
        self.channel_ = FakeChannel()

    def channel(self):
        return self.channel_

    def close(self):
        self.is_closed, self.is_open = True, False


def test_heartbeat_is_sent_without_mandatory_and_not_persistent(monkeypatch):
    """Heartbeats go to a fanout exchange, transient, without mandatory."""
    monkeypatch.setattr(monitoring.pika, 'BlockingConnection', FakeConnection)
    heartbeat = TokeoDramatiqHeartbeat('probe.dramatiq.monitoring', 5, dict(label='probe', version=None), False)
    heartbeat._parameters = object()
    heartbeat._send(dict(a=1))
    heartbeat._send(dict(a=2))
    channel = heartbeat._connection.channel_
    assert channel.declared == [('probe.dramatiq.monitoring', 'fanout')]
    first = channel.published[0]
    assert (first['exchange'], first['routing_key'], json.loads(first['body'])) == ('probe.dramatiq.monitoring', '', dict(a=1))
    assert first['properties'].delivery_mode == 1 and 'mandatory' not in first
    assert len(channel.published) == 2


def test_heartbeat_survives_a_lost_rabbitmq(monkeypatch):
    """Without rabbitmq the heartbeat is dropped quietly."""

    def refused(parameters):
        raise pika.exceptions.AMQPConnectionError('refused')

    monkeypatch.setattr(monitoring.pika, 'BlockingConnection', refused)
    heartbeat = TokeoDramatiqHeartbeat('probe.dramatiq.monitoring', 5, dict(label='probe', version=None), False)
    heartbeat._parameters = object()
    heartbeat._send(dict(a=1))
    assert heartbeat._connection is None


def test_heartbeat_without_broker_parameters_sends_nothing(monkeypatch):
    """Without broker parameters the heartbeat connects nowhere."""
    monkeypatch.setattr(monitoring.pika, 'BlockingConnection', lambda parameters: pytest.fail('connected'))
    TokeoDramatiqHeartbeat('probe.dramatiq.monitoring', 5, {}, False)._send(dict(a=1))


# ---- monitor ----------------------------------------------------------------


def _make_beat(pid, interval_seconds=5, state='running', running=(), since=None, with_details=False):
    return dict(
        type='tokeo.dramatiq.heartbeat',
        version=1,
        state=state,
        interval_seconds=interval_seconds,
        sent_at=1_000_000,
        app=dict(label='probe', version=None),
        worker=dict(
            host='w1',
            pid=pid,
            started_at=1_000,
            threads=2,
            queues=None,
            queue_prefetch=4,
            delay_queue_prefetch=8,
            with_details=with_details,
        ),
        running=list(running),
        totals=dict(done=3, failed=1, dead=0),
        since_last=dict(ms=5000, actors=since or {}),
    )


@pytest.fixture
def monitor(tmp, monkeypatch):
    """The monitor of an app, fed by receive and with a fixed queue state."""
    with DramatiqMiddlewareTest(tmp) as app:
        monitor = app.dramatiq.monitor
        # heartbeats come by receive, not by rabbitmq
        monkeypatch.setattr(monitor, '_collect', lambda wait: None)
        monkeypatch.setattr(app.dramatiq.queues, 'state', lambda: [dict(name='mail', ready=2, delayed=None, dead=1, consumers=1)])
        yield monitor


def test_monitor_states(monitor):
    """A worker is late after 2 and gone after 3 of its intervals."""
    now = time.monotonic()
    beats = [(1, 1, 5, 'running'), (2, 12, 5, 'running'), (3, 16, 5, 'running'), (4, 3, 1, 'running'), (5, 2, 5, 'stopped')]
    for pid, seen, interval, state in beats:
        monitor.receive(json.dumps(_make_beat(pid, interval, state)), received_time=now - seen)
    states = {w['heartbeat']['worker']['pid']: w['state'] for w in monitor.workers()}
    # late after 2 and gone after 3 intervals, each by the interval of the worker
    assert states == {1: 'ok', 2: 'late', 3: 'gone', 4: 'gone', 5: 'stopped'}
    assert next(w for w in monitor.workers() if w['heartbeat']['worker']['pid'] == 2)['seen_ms'] >= 12_000


def test_monitor_drops_gone_and_stopped_workers(monitor):
    """Gone and stopped workers stay for 10 more intervals."""
    now = time.monotonic()
    for pid, seen, state in [(1, 64, 'running'), (2, 66, 'running'), (3, 49, 'stopped'), (4, 51, 'stopped')]:
        monitor.receive(json.dumps(_make_beat(pid, state=state)), received_time=now - seen)
    # gone and stopped workers stay for 10 more intervals
    assert [w['heartbeat']['worker']['pid'] for w in monitor.workers()] == [1, 3]


def test_monitor_ignores_other_messages(monitor):
    """Messages that are no heartbeat are ignored."""
    for body in (b'not json', b'{}', json.dumps(dict(_make_beat(1), type='other')), b'[1]'):
        monitor.receive(body)
    assert monitor.workers() == []


def test_monitor_snapshot_sums_running_workers(monitor):
    """The snapshot sums the actors of live workers and the queues."""
    now = time.monotonic()
    since = dict(send_mail=dict(done=2, failed=1, max_runtime_ms=1400, last_error='LookupError'))
    monitor.receive(json.dumps(_make_beat(1, since=since)), received_time=now)
    monitor.receive(json.dumps(_make_beat(2, since=dict(send_mail=dict(done=1, failed=0, max_runtime_ms=300)))), received_time=now)
    monitor.receive(json.dumps(_make_beat(3, since=since)), received_time=now - 20)
    snapshot = monitor.snapshot()
    assert snapshot['monitoring'] is False and snapshot['interval_seconds'] == 5
    # the gone worker counts no more
    assert snapshot['actors'] == dict(send_mail=dict(done=3, failed=1, max_runtime_ms=1400, last_error='LookupError'))
    assert snapshot['queue_totals'] == dict(ready=2, delayed=0, dead=1, consumers=1)


def test_monitor_without_rabbitmq(tmp, monkeypatch):
    """Without rabbitmq the monitor raises the connection error."""

    def refused(parameters):
        raise pika.exceptions.AMQPConnectionError('refused')

    monkeypatch.setattr(monitoring.pika, 'BlockingConnection', refused)
    with DramatiqMiddlewareTest(tmp) as app:
        with pytest.raises(TokeoDramatiqConnectionError, match='Cannot connect to rabbitmq at localhost:5672'):
            app.dramatiq.monitor.workers()


# ---- monitor command --------------------------------------------------------


def test_duration():
    """Durations show as "850 ms", "1.4 s", "3m 5s" or "1h 2m"."""
    assert [_format_duration(ms) for ms in (850, 1400, 185_000, 3_720_000)] == ['850 ms', '1.4 s', '3m 5s', '1h 2m']


def _render_view(app, snapshot, interval_seconds=None):
    output = app.tui.output
    return output.render(*_format_monitor_view(output, snapshot, 'probe', interval_seconds), width=140)


def _make_snapshot(workers, monitoring=True, actors=None):
    return dict(
        at=1_000 + 65_000,
        monitoring=monitoring,
        interval_seconds=5,
        workers=workers,
        actors=actors or {},
        queues=[dict(name='mail', ready=2, delayed=0, dead=1, consumers=1)],
        queue_totals=dict(ready=2, delayed=0, dead=1, consumers=1),
    )


def test_format_monitor_view(tmp):
    """The view shows workers, running tasks, actors and the queue totals."""
    task = dict(message_id='m1', actor_name='send_mail', queue_name='mail', retries=1, started_at=0, runtime_ms=1200)
    running = [dict(task, args="(('[red]anna',), {})")]
    since = dict(send_mail=dict(done=2, failed=1, max_runtime_ms=1400, last_error='LookupError'))
    workers = [
        dict(state='ok', seen_ms=300, heartbeat=_make_beat(4711, running=running, since=since, with_details=True)),
        # a worker without details sends no args
        dict(state='gone', seen_ms=20_000, heartbeat=_make_beat(4712, running=[{k: v for k, v in running[0].items() if k != 'args'}])),
    ]
    with DramatiqMiddlewareTest(tmp) as app:
        text = _render_view(app, _make_snapshot(workers, actors=since), interval_seconds=5)
    assert text.startswith('probe dramatiq monitor  ') and 'every 5 s  (Ctrl-C to quit)' in text
    worker_line = next(line for line in text.splitlines() if 'w1:4711' in line and 'ok' in line)
    assert worker_line.split() == ['w1:4711', '1m', '5s', 'ok', '1/2', '3', '1', '0', '2', 'done,', '1', 'failed']
    # the runtime goes on since the heartbeat, the args are shown as they are
    task_line = next(line for line in text.splitlines() if 'send_mail' in line and '1.5 s' in line)
    assert "(('[red]anna',), {})" in task_line
    assert 'details off' in next(line for line in text.splitlines() if 'w1:4712' in line and 'send_mail' in line)
    assert next(line for line in text.splitlines() if line.strip().startswith('send_mail') and 'LookupError' in line).split() == [
        'send_mail',
        '2',
        '1',
        '1.4',
        's',
        'LookupError',
    ]
    assert next(line for line in text.splitlines() if line.strip().startswith('total')).split() == ['total', '2', '0', '1', '1']


@pytest.mark.parametrize('monitoring, note', [(True, 'No worker heard of yet.'), (False, 'Monitoring is off in the config')])
def test_format_monitor_view_without_workers(tmp, monitoring, note):
    """Without workers the view says why, with monitoring on or off."""
    with DramatiqMiddlewareTest(tmp) as app:
        text = _render_view(app, _make_snapshot([], monitoring=monitoring))
    assert note in text and 'every' not in text


@pytest.fixture
def command(tmp, monkeypatch):
    """Runs the monitor command on a fixed snapshot."""

    def run(*options):
        with DramatiqMiddlewareTest(tmp, ['dramatiq', 'monitor', *options]) as app:
            calls = []

            def snapshot(wait_seconds=0):
                calls.append(wait_seconds)
                return _make_snapshot([dict(state='ok', seen_ms=0, heartbeat=_make_beat(4711))])

            monkeypatch.setattr(app.dramatiq.monitor, 'snapshot', snapshot)
            follows = []
            monkeypatch.setattr(app.tui.output, 'follow', lambda render, **kw: follows.append((render(), kw)))
            app.run()
            return app, calls, follows

    return run


def test_command_json(command):
    """dramatiq monitor --json prints one snapshot as json."""
    app, calls, _ = command('--json')
    assert json.loads(app.last_rendered[1])['workers'][0]['heartbeat']['worker']['pid'] == 4711
    assert calls == [0]


def test_needs_the_tui_output_extension(tmp):
    """Without tokeo.ext.tui.output the app names the missing extension."""
    with pytest.raises(TokeoError, match='The extension tokeo.ext.dramatiq needs tokeo.ext.tui.output, add it to the extensions'):
        with DramatiqWithoutTuiTest(tmp):
            pass


def test_command_text(command, capsys):
    """dramatiq monitor prints the view once."""
    command()
    assert 'w1:4711' in capsys.readouterr().out


def test_command_follow(command):
    """--follow --interval 2 listens 2 s between the views."""
    app, calls, follows = command('--follow', '--interval', '2')
    ((view, options),) = follows
    # listening for the interval fills the time between two displays
    assert calls == [2] and options == dict(interval_seconds=0)
    assert 'every 2 s' in TokeoTuiOutput(None).render(*view)


def test_command_follow_json(command):
    """--follow --json prints one json line per interval, appended."""
    app, calls, follows = command('--follow', '--json')
    ((line, options),) = follows
    assert json.loads(line)['interval_seconds'] == 5 and '\n' not in line
    assert calls == [5] and options == dict(interval_seconds=0, with_repaint=False)


def test_command_interval_checked(command):
    """--interval 0 raises the config error."""
    with pytest.raises(TokeoDramatiqConfigError, match='Invalid switch --interval'):
        command('--follow', '--interval', '0')


@needs_pty
def test_command_follow_ends_on_ctrl_c_in_a_terminal(tmp):
    """
    The probe follows the monitor in a terminal; Ctrl-C ends it within
    5 s with 0 and brings back the former screen.
    """
    probe = 'tests.ext.dramatiq.lib.dramatiq_tests_monitor_probe'
    args = [sys.executable, '-m', probe, tmp.dir, 'dramatiq', 'monitor', '--follow', '--interval', '1']
    with Terminal(args, rows=24, columns=100, ctty=True) as terminal:
        shown = terminal.wait_for(lambda lines: any('every 1 s  (Ctrl-C to quit)' in line for line in lines))
        assert shown is not None, terminal.raw.decode(errors='replace')[-2000:]
        terminal.send(b'\x03')
        sent_time = time.monotonic()
        code = terminal.wait()
    assert shown[0].startswith('probe dramatiq monitor') and any(line.split() == ['total', '0', '0', '0', '1'] for line in shown)
    # Ctrl-C ends the display at once and cleanly, the former screen is back
    assert code == 0 and time.monotonic() - sent_time < 5
    assert b'\x1b[?1049h' in terminal.raw and b'\x1b[?1049l' in terminal.raw and b'Traceback' not in terminal.raw
    assert not any('dramatiq monitor' in line for line in terminal.screen.display)
