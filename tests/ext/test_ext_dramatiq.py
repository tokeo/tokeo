"""
Tests for tokeo.ext.dramatiq without a running broker.

The dramatiq cli is replaced by a recorder, so the tests check what the serve
command hands over to dramatiq and what it does before the workers start.
"""

import os
import sys

import pytest
from cement.utils.misc import init_defaults
from dramatiq import cli as dramatiq_cli
from dramatiq import worker as dramatiq_worker
from dramatiq.brokers import rabbitmq as dramatiq_rabbitmq
from tokeo.main import TokeoTest
from dramatiq.asyncio import get_event_loop_thread
from tokeo.ext.dramatiq import TokeoDramatiqError, TokeoAsyncIO
from tests.ext.test_ext_smtp import make_cert


class DramatiqTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.diskcache',
            'tokeo.ext.dramatiq',
        ]


def dramatiq_app(tmp, argv):
    d = init_defaults('diskcache')
    d['diskcache']['directory'] = tmp.dir
    return DramatiqTest(argv=argv, config_defaults=d)


@pytest.fixture
def serve(tmp, monkeypatch):
    """Run the serve command with a recording cli and purge."""
    # serve rewrites sys.argv, the env and the dramatiq worker constants;
    # monkeypatch restores all of them after the test
    monkeypatch.setattr(sys, 'argv', ['tokeo'])
    for name in ('dramatiq_restart_delay', 'dramatiq_queue_prefetch', 'dramatiq_delay_queue_prefetch', 'dramatiq_worker_timeout'):
        monkeypatch.delenv(name, raising=False)
    for name in ('CONSUMER_RESTART_DELAY', 'CONSUMER_RESTART_DELAY_SECS', 'QUEUE_PREFETCH', 'DELAY_QUEUE_PREFETCH'):
        monkeypatch.setattr(dramatiq_worker, name, getattr(dramatiq_worker, name))
    calls = dict(main=[], purge=0, argv=None, start_methods=[])

    def fake_main(args):
        calls['main'].append(args)
        return 0

    monkeypatch.setattr('tokeo.ext.dramatiq.cli.main', fake_main)
    make_argument_parser = dramatiq_cli.make_argument_parser

    def recording_parser():
        # serve builds the argv for dramatiq right before the parser
        calls['dramatiq_argv'] = list(sys.argv)
        return make_argument_parser()

    monkeypatch.setattr('tokeo.ext.dramatiq.cli.make_argument_parser', recording_parser)

    def fake_set_start_method(method, force=False):
        # never change the start method of the pytest process itself
        calls['start_methods'].append((method, force))

    monkeypatch.setattr('tokeo.ext.dramatiq.multiprocessing.set_start_method', fake_set_start_method)

    def run(*options, config=None, debug=False):
        with dramatiq_app(tmp, ['--debug'] * debug + ['dramatiq', 'serve', *options]) as app:
            for key, value in (config or {}).items():
                app.config.set('dramatiq', key, value)

            def fake_purge():
                calls['purge'] += 1
                return 0

            monkeypatch.setattr(app.dramatiq.locks, 'purge', fake_purge)
            app.run()
            calls['argv'] = list(sys.argv)
            calls['exit_code'] = app.exit_code
        return calls

    return run


def test_serve_purges_locks(serve):
    calls = serve()
    assert calls['purge'] == 1
    assert len(calls['main']) == 1
    assert calls['exit_code'] == 0


def test_serve_no_purge_keeps_locks(serve):
    calls = serve('--no-purge')
    assert calls['purge'] == 0
    assert len(calls['main']) == 1


def test_serve_no_purge_survives_watch_restart(serve):
    # a restart by --watch re-executes sys.argv, so the restored argv must
    # still carry --no-purge for the next start of the service
    calls = serve('--no-purge', '--watch', __file__)
    assert calls['argv'] == ['tokeo', 'dramatiq', 'serve', '--no-purge', '--watch', __file__]
    assert calls['main'][0].watch is not None


# --- worker options ------------------------------------------------------------


def test_serve_worker_options_from_config(serve):
    calls = serve()
    args = calls['main'][0]
    # the defaults of the extension reach dramatiq
    assert (args.processes, args.threads, args.worker_shutdown_timeout) == (2, 1, 600_000)
    # auto in the config hands 0 to dramatiq, which follows the threads
    assert (dramatiq_worker.QUEUE_PREFETCH, dramatiq_worker.DELAY_QUEUE_PREFETCH) == (0, 0)
    assert os.environ['dramatiq_queue_prefetch'] == '0'


def test_serve_worker_switches_replace_config(serve):
    config = dict(
        worker_processes=3,
        worker_threads=4,
        worker_shutdown_timeout=5000,
        queue_prefetch=10,
        delay_queue_prefetch=20,
    )
    calls = serve(
        '-p',
        '5',
        '-t',
        '6',
        '--worker-shutdown-timeout',
        '7000',
        '--queue-prefetch',
        '8',
        '--delay-queue-prefetch',
        '9',
        config=config,
    )
    args = calls['main'][0]
    assert (args.processes, args.threads, args.worker_shutdown_timeout) == (5, 6, 7000)
    assert (dramatiq_worker.QUEUE_PREFETCH, dramatiq_worker.DELAY_QUEUE_PREFETCH) == (8, 9)
    assert (os.environ['dramatiq_queue_prefetch'], os.environ['dramatiq_delay_queue_prefetch']) == ('8', '9')


def test_serve_fixed_config_values_without_switches(serve):
    calls = serve(config=dict(worker_processes=3, queue_prefetch=10, delay_queue_prefetch='20'))
    assert calls['main'][0].processes == 3
    assert (dramatiq_worker.QUEUE_PREFETCH, dramatiq_worker.DELAY_QUEUE_PREFETCH) == (10, 20)


def test_serve_auto_switches_reset_fixed_config(serve):
    config = dict(worker_processes=3, queue_prefetch=10, delay_queue_prefetch=20)
    calls = serve('-p', 'auto', '--queue-prefetch', 'auto', '--delay-queue-prefetch', 'AUTO', config=config)
    # without --processes dramatiq takes its default, the cpus of the host
    assert '--processes' not in calls['dramatiq_argv']
    assert calls['main'][0].processes == dramatiq_cli.CPUS
    assert (dramatiq_worker.QUEUE_PREFETCH, dramatiq_worker.DELAY_QUEUE_PREFETCH) == (0, 0)


@pytest.mark.parametrize('value', ['auto', 0, '0'])
def test_serve_auto_prefetch_in_config(serve, value):
    serve(config=dict(queue_prefetch=value, delay_queue_prefetch=value))
    assert (dramatiq_worker.QUEUE_PREFETCH, dramatiq_worker.DELAY_QUEUE_PREFETCH) == (0, 0)


def test_serve_auto_processes_in_config(serve):
    calls = serve(config=dict(worker_processes='auto'))
    assert '--processes' not in calls['dramatiq_argv']
    assert calls['main'][0].processes == dramatiq_cli.CPUS


@pytest.mark.parametrize(
    'options',
    [
        ['-p', '0'],
        ['-p', 'x'],
        ['-t', 'auto'],
        ['-t', '0'],
        ['--queue-prefetch', '0'],
        ['--delay-queue-prefetch', '-1'],
        ['--worker-shutdown-timeout', 'auto'],
        ['--worker-shutdown-timeout', '1.5'],
    ],
    ids=lambda options: ' '.join(options),
)
def test_serve_invalid_switches(serve, capsys, options):
    with pytest.raises(SystemExit):
        serve(*options)
    assert 'must be' in capsys.readouterr().err
    assert sys.argv == ['tokeo']


@pytest.mark.parametrize(
    'key, value',
    [
        ('worker_processes', 0),
        ('worker_threads', 0),
        ('worker_threads', 'auto'),
        ('worker_shutdown_timeout', 'auto'),
        ('queue_prefetch', -1),
        ('delay_queue_prefetch', 'x'),
    ],
)
def test_serve_invalid_config(serve, key, value):
    with pytest.raises(TokeoDramatiqError, match=f'Invalid dramatiq config {key}'):
        serve(config={key: value})
    # the error comes before sys.argv is changed and dramatiq is called
    assert sys.argv == ['tokeo']


# --- start method --------------------------------------------------------------


def test_serve_starts_workers_by_forkserver(serve):
    calls = serve()
    assert calls['start_methods'] == [('forkserver', True)]


def test_serve_falls_back_to_spawn_without_forkserver(serve, monkeypatch):
    monkeypatch.setattr('tokeo.ext.dramatiq.multiprocessing.get_all_start_methods', lambda: ['spawn'])
    calls = serve()
    assert calls['start_methods'] == [('spawn', True)]


@pytest.mark.parametrize(
    'options, config, method',
    [
        ([], dict(use_spawn=True), 'spawn'),
        (['--use-spawn'], {}, 'spawn'),
        (['--use-forkserver'], dict(use_spawn=True), 'forkserver'),
        (['--use-spawn', '--use-forkserver'], {}, 'forkserver'),
    ],
    ids=['config', 'switch', 'forkserver_switch', 'last_wins'],
)
def test_serve_use_spawn(serve, options, config, method):
    calls = serve(*options, config=config)
    assert calls['start_methods'] == [(method, True)]
    # tokeo sets the start method itself, dramatiq never gets the switch
    assert '--use-spawn' not in calls['dramatiq_argv']
    # a restart by --watch keeps the switches
    assert calls['argv'] == ['tokeo', 'dramatiq', 'serve', *options]


@pytest.mark.parametrize('value', ['yes', 1, None])
def test_serve_invalid_use_spawn_config(serve, value):
    with pytest.raises(TokeoDramatiqError, match='Invalid dramatiq config use_spawn'):
        serve(config=dict(use_spawn=value))
    assert sys.argv == ['tokeo']


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


def test_register_uses_tokeo_asyncio(tmp):
    import dramatiq

    with dramatiq_app(tmp, []):
        kinds = [type(m) for m in dramatiq.get_broker().middleware]
    assert TokeoAsyncIO in kinds


# --- verbosity -----------------------------------------------------------------


@pytest.mark.parametrize(
    'options, debug, verbose, skip_logging',
    [
        ([], False, 0, False),
        ([], True, 1, False),
        (['--skip-logging'], True, 0, True),
        (['--skip-logging'], False, 0, True),
    ],
    ids=['default', 'debug', 'debug_skip_logging', 'skip_logging'],
)
def test_serve_verbose_follows_debug(serve, options, debug, verbose, skip_logging):
    calls = serve(*options, debug=debug)
    args = calls['main'][0]
    assert (args.verbose, args.skip_logging) == (verbose, skip_logging)


# --- queue binding -------------------------------------------------------------


def test_serve_consumes_all_queues_without_switch(serve):
    calls = serve()
    assert '--queues' not in calls['dramatiq_argv']
    assert not calls['main'][0].queues


def test_serve_binds_queues(serve):
    calls = serve('--queues', 'mail', 'orders_unparalleled')
    args = calls['main'][0]
    # --queues stays behind broker and actors, so dramatiq keeps both
    assert calls['dramatiq_argv'][-3:] == ['--queues', 'mail', 'orders_unparalleled']
    assert (args.broker, args.modules) == ('main:method', ['actors.module'])
    assert args.queues == ['mail', 'orders_unparalleled']
    # a restart by --watch keeps the binding
    assert calls['argv'] == ['tokeo', 'dramatiq', 'serve', '--queues', 'mail', 'orders_unparalleled']


def test_serve_queues_needs_a_name(serve, capsys):
    with pytest.raises(SystemExit):
        serve('--queues')
    assert '--queues' in capsys.readouterr().err


# --- broker --------------------------------------------------------------------


def broker_app(tmp, rabbitmq):
    d = init_defaults('diskcache', 'dramatiq')
    d['diskcache']['directory'] = tmp.dir
    if rabbitmq is not None:
        d['dramatiq']['rabbitmq'] = rabbitmq
    return DramatiqTest(argv=[], config_defaults=d)


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
    import dramatiq

    with broker_app(tmp, rabbitmq):
        assert dramatiq.get_broker().confirm_delivery is confirm


def test_register_confirm_delivery_with_tls_parameters(tmp):
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
def test_register_invalid_confirm_delivery(tmp, value):
    with pytest.raises(TokeoDramatiqError, match='Invalid dramatiq config rabbitmq.confirm_delivery'):
        with broker_app(tmp, dict(confirm_delivery=value)):
            pass


@pytest.fixture
def keep_dead_message_ttl(monkeypatch):
    # register sets the dramatiq constant, restore it after the test
    monkeypatch.setattr(dramatiq_rabbitmq, 'DEAD_MESSAGE_TTL', dramatiq_rabbitmq.DEAD_MESSAGE_TTL)
    return dramatiq_rabbitmq.DEAD_MESSAGE_TTL


@pytest.mark.parametrize('rabbitmq', [None, dict(url='amqp://localhost:5672/')], ids=['default', 'partial_block'])
def test_register_queue_arguments_default(tmp, keep_dead_message_ttl, rabbitmq):
    import dramatiq

    with broker_app(tmp, rabbitmq):
        broker = dramatiq.get_broker()
        # without config the queues look as before: no priority, ttl of dramatiq
        assert broker.max_priority is None
        assert 'x-max-priority' not in broker._build_queue_arguments('mail')
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == keep_dead_message_ttl


def test_register_queue_arguments_from_config(tmp, keep_dead_message_ttl):
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
def test_register_invalid_queue_arguments(tmp, keep_dead_message_ttl, key, value):
    with pytest.raises(TokeoDramatiqError, match=f'Invalid dramatiq config rabbitmq.{key}'):
        with broker_app(tmp, {key: value}):
            pass
    assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == keep_dead_message_ttl


# --- middleware defaults -------------------------------------------------------


def middleware_app(tmp, **config):
    d = init_defaults('diskcache', 'dramatiq')
    d['diskcache']['directory'] = tmp.dir
    d['dramatiq'].update(config)
    return DramatiqTest(argv=[], config_defaults=d)


def middleware_of(kind):
    import dramatiq

    return [m for m in dramatiq.get_broker().middleware if isinstance(m, kind)][0]


def test_register_middleware_defaults(tmp):
    from dramatiq import middleware

    with middleware_app(tmp):
        # the defaults are the ones of dramatiq, so nothing changes for apps
        assert middleware_of(middleware.TimeLimit).time_limit == 600_000
        assert middleware_of(middleware.AgeLimit).max_age is None
        assert middleware_of(middleware.ShutdownNotifications).notify_shutdown is False
        retries = middleware_of(middleware.Retries)
        assert (retries.max_retries, retries.min_backoff, retries.max_backoff) == (20, 15_000, 604_800_000)


def test_register_middleware_from_config(tmp):
    from dramatiq import middleware

    config = dict(time_limit=5000, max_age='60000', notify_shutdown=True, max_retries=0, min_backoff=200, max_backoff=1000)
    with middleware_app(tmp, **config):
        assert middleware_of(middleware.TimeLimit).time_limit == 5000
        assert middleware_of(middleware.AgeLimit).max_age == 60000
        assert middleware_of(middleware.ShutdownNotifications).notify_shutdown is True
        retries = middleware_of(middleware.Retries)
        assert (retries.max_retries, retries.min_backoff, retries.max_backoff) == (0, 200, 1000)


def test_register_actor_options_stay_available(tmp):
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
def test_register_invalid_middleware_defaults(tmp, key, value):
    with pytest.raises(TokeoDramatiqError, match=f'Invalid dramatiq config {key}'):
        with middleware_app(tmp, **{key: value}):
            pass


# --- worker poll interval ------------------------------------------------------


def test_serve_poll_interval_default_keeps_env(serve):
    serve()
    # without config tokeo leaves the env of dramatiq alone
    assert 'dramatiq_worker_timeout' not in os.environ


def test_serve_poll_interval_from_config(serve, monkeypatch):
    # the config wins over an env set from outside, even an invalid one
    monkeypatch.setenv('dramatiq_worker_timeout', '0')
    serve(config=dict(worker_poll_interval=250))
    assert os.environ['dramatiq_worker_timeout'] == '250'


def test_serve_poll_interval_valid_env(serve, monkeypatch):
    monkeypatch.setenv('dramatiq_worker_timeout', '500')
    calls = serve()
    assert calls['main']
    assert os.environ['dramatiq_worker_timeout'] == '500'


@pytest.mark.parametrize('value', ['0', '-5', '1s'])
def test_serve_poll_interval_invalid_env(serve, monkeypatch, value):
    monkeypatch.setenv('dramatiq_worker_timeout', value)
    with pytest.raises(TokeoDramatiqError, match='Invalid env dramatiq_worker_timeout'):
        serve()
    assert sys.argv == ['tokeo']


@pytest.mark.parametrize('value', [0, 'fast', True])
def test_serve_poll_interval_invalid_config(serve, value):
    with pytest.raises(TokeoDramatiqError, match='Invalid dramatiq config worker_poll_interval'):
        serve(config=dict(worker_poll_interval=value))


# --- dead message ttl env ------------------------------------------------------


def test_register_dead_message_ttl_valid_env(tmp, keep_dead_message_ttl, monkeypatch):
    monkeypatch.setenv('dramatiq_dead_message_ttl', '86400000')
    with broker_app(tmp, None):
        # dramatiq read the env on import already, tokeo only checks it
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == keep_dead_message_ttl


@pytest.mark.parametrize('value', ['0', '-1', 'week'])
def test_register_dead_message_ttl_invalid_env(tmp, keep_dead_message_ttl, monkeypatch, value):
    monkeypatch.setenv('dramatiq_dead_message_ttl', value)
    with pytest.raises(TokeoDramatiqError, match='Invalid env dramatiq_dead_message_ttl'):
        with broker_app(tmp, None):
            pass


def test_register_dead_message_ttl_config_wins_over_env(tmp, keep_dead_message_ttl, monkeypatch):
    # with the config value the env does not matter, even an invalid one
    monkeypatch.setenv('dramatiq_dead_message_ttl', '0')
    with broker_app(tmp, dict(dead_message_ttl=60000)):
        assert dramatiq_rabbitmq.DEAD_MESSAGE_TTL == 60000
