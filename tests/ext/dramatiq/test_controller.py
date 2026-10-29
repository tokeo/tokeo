"""
Tests for tokeo.ext.dramatiq.controller without a running broker: a recorder
takes the place of the dramatiq cli, so the tests check what the serve command
hands over to dramatiq and what it does before the workers start.
"""

import os
import sys

import pytest
from dramatiq import broker as dramatiq_broker
from dramatiq import cli as dramatiq_cli
from tokeo.ext.dramatiq import TokeoDramatiqConfigError
from tests.ext.dramatiq.lib.dramatiq_tests_helper import DramatiqTest


@pytest.fixture(autouse=True)
def keep_global_broker(monkeypatch):
    """The global broker of dramatiq, restored after the test, register sets it."""
    monkeypatch.setattr(dramatiq_broker, 'global_broker', dramatiq_broker.global_broker)


@pytest.fixture
def serve(tmp, monkeypatch):
    """Runs the serve command with a recording cli and purge."""
    # serve rewrites sys.argv and the env, monkeypatch restores both
    monkeypatch.setattr(sys, 'argv', ['tokeo'])
    for name in ('dramatiq_restart_delay', 'dramatiq_queue_prefetch', 'dramatiq_delay_queue_prefetch', 'dramatiq_worker_timeout'):
        # announced first, so a variable that serve sets is removed again
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)
    calls = dict(main=[], purge=0, argv=None, start_methods=[])

    def fake_main(args):
        calls['main'].append(args)
        return 0

    monkeypatch.setattr('tokeo.ext.dramatiq.controller.cli.main', fake_main)
    make_argument_parser = dramatiq_cli.make_argument_parser

    def recording_parser():
        # serve builds the argv for dramatiq right before the parser
        calls['dramatiq_argv'] = list(sys.argv)
        return make_argument_parser()

    monkeypatch.setattr('tokeo.ext.dramatiq.controller.cli.make_argument_parser', recording_parser)

    def fake_set_start_method(method, force=False):
        # never change the start method of the pytest process itself
        calls['start_methods'].append((method, force))

    monkeypatch.setattr('tokeo.ext.dramatiq.controller.multiprocessing.set_start_method', fake_set_start_method)

    def run(*options, config=None, debug=False):
        with DramatiqTest(tmp, ['--debug'] * debug + ['dramatiq', 'serve', *options]) as app:
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
    """serve purges the locks once and starts dramatiq."""
    calls = serve()
    assert calls['purge'] == 1
    assert len(calls['main']) == 1
    assert calls['exit_code'] == 0


def test_serve_no_purge_keeps_locks(serve):
    """serve --no-purge keeps the locks."""
    calls = serve('--no-purge')
    assert calls['purge'] == 0
    assert len(calls['main']) == 1


def test_serve_no_purge_survives_watch_restart(serve):
    """The argv kept for a restart by --watch still holds --no-purge."""
    calls = serve('--no-purge', '--watch', __file__)
    assert calls['argv'] == ['tokeo', 'dramatiq', 'serve', '--no-purge', '--watch', __file__]
    assert calls['main'][0].watch is not None


# ---- worker options -----------------------------------------------------------


def test_serve_worker_options_from_config(serve):
    """Without switches dramatiq gets the defaults, prefetch auto as 0."""
    calls = serve()
    args = calls['main'][0]
    # the defaults of the extension reach dramatiq
    assert (args.processes, args.threads, args.worker_shutdown_timeout) == (2, 1, 600_000)
    # auto in the config hands 0 to dramatiq, which follows the threads
    assert (os.environ['dramatiq_queue_prefetch'], os.environ['dramatiq_delay_queue_prefetch']) == ('0', '0')


def test_serve_worker_switches_replace_config(serve):
    """The switches win over the config for workers and prefetch."""
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
    assert (os.environ['dramatiq_queue_prefetch'], os.environ['dramatiq_delay_queue_prefetch']) == ('8', '9')


def test_serve_fixed_config_values_without_switches(serve):
    """Fixed config values reach dramatiq and its env."""
    calls = serve(config=dict(worker_processes=3, queue_prefetch=10, delay_queue_prefetch='20'))
    assert calls['main'][0].processes == 3
    assert (os.environ['dramatiq_queue_prefetch'], os.environ['dramatiq_delay_queue_prefetch']) == ('10', '20')


def test_serve_auto_switches_reset_fixed_config(serve):
    """auto as switch resets a fixed config, processes follow the cpus."""
    config = dict(worker_processes=3, queue_prefetch=10, delay_queue_prefetch=20)
    calls = serve('-p', 'auto', '--queue-prefetch', 'auto', '--delay-queue-prefetch', 'AUTO', config=config)
    # without --processes dramatiq takes its default, the cpus of the host
    assert '--processes' not in calls['dramatiq_argv']
    assert calls['main'][0].processes == dramatiq_cli.CPUS
    assert (os.environ['dramatiq_queue_prefetch'], os.environ['dramatiq_delay_queue_prefetch']) == ('0', '0')


@pytest.mark.parametrize('value', ['auto', 0, '0'])
def test_serve_auto_prefetch_in_config(serve, value):
    """auto, 0 or "0" as prefetch in the config give 0."""
    serve(config=dict(queue_prefetch=value, delay_queue_prefetch=value))
    assert (os.environ['dramatiq_queue_prefetch'], os.environ['dramatiq_delay_queue_prefetch']) == ('0', '0')


def test_serve_auto_processes_in_config(serve):
    """worker_processes auto lets dramatiq take the cpus."""
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
def test_serve_invalid_switches(serve, options):
    """An invalid switch raises the config error naming the switch."""
    switch = {'-p': '--processes', '-t': '--threads'}.get(options[0], options[0])
    with pytest.raises(TokeoDramatiqConfigError, match=f'Invalid switch {switch}: Value .* must be'):
        serve(*options)
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
    """An invalid config value raises before sys.argv changes."""
    with pytest.raises(TokeoDramatiqConfigError, match=f'Invalid dramatiq config {key}'):
        serve(config={key: value})
    # the error comes before sys.argv is changed and dramatiq is called
    assert sys.argv == ['tokeo']


# ---- start method -------------------------------------------------------------


def test_serve_starts_workers_by_forkserver(serve):
    """serve sets the start method forkserver."""
    calls = serve()
    assert calls['start_methods'] == [('forkserver', True)]


def test_serve_falls_back_to_spawn_without_forkserver(serve, monkeypatch):
    """Without forkserver serve sets spawn."""
    monkeypatch.setattr('tokeo.ext.dramatiq.controller.multiprocessing.get_all_start_methods', lambda: ['spawn'])
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
    """use_spawn and the switches pick the start method, the last wins."""
    calls = serve(*options, config=config)
    assert calls['start_methods'] == [(method, True)]
    # tokeo sets the start method itself, dramatiq never gets the switch
    assert '--use-spawn' not in calls['dramatiq_argv']
    # a restart by --watch keeps the switches
    assert calls['argv'] == ['tokeo', 'dramatiq', 'serve', *options]


@pytest.mark.parametrize('value', ['yes', 1, None])
def test_serve_invalid_use_spawn_config(serve, value):
    """use_spawn other than a bool raises the config error."""
    with pytest.raises(TokeoDramatiqConfigError, match='Invalid dramatiq config use_spawn'):
        serve(config=dict(use_spawn=value))
    assert sys.argv == ['tokeo']


# ---- verbosity ----------------------------------------------------------------


# cement 3.0.x warns on --debug in sys.argv, our --debug is the app debug only
DEBUG_ARGV = pytest.mark.filterwarnings(r'ignore:App\.Meta\.framework_logging:DeprecationWarning')


@pytest.mark.parametrize(
    'options, debug, verbose, skip_logging',
    [
        ([], False, 0, False),
        pytest.param([], True, 1, False, marks=DEBUG_ARGV),
        pytest.param(['--skip-logging'], True, 0, True, marks=DEBUG_ARGV),
        (['--skip-logging'], False, 0, True),
    ],
    ids=['default', 'debug', 'debug_skip_logging', 'skip_logging'],
)
def test_serve_verbose_follows_debug(serve, options, debug, verbose, skip_logging):
    """dramatiq is verbose with --debug unless --skip-logging."""
    calls = serve(*options, debug=debug)
    args = calls['main'][0]
    assert (args.verbose, args.skip_logging) == (verbose, skip_logging)


# ---- queue binding ------------------------------------------------------------


def test_serve_consumes_all_queues_without_switch(serve):
    """Without --queues dramatiq consumes all queues."""
    calls = serve()
    assert '--queues' not in calls['dramatiq_argv']
    assert not calls['main'][0].queues


def test_serve_binds_queues(serve):
    """--queues reaches dramatiq after broker and actors and survives a restart."""
    calls = serve('--queues', 'mail', 'orders_unparalleled')
    args = calls['main'][0]
    # --queues stays behind broker and actors, so dramatiq keeps both
    assert calls['dramatiq_argv'][-3:] == ['--queues', 'mail', 'orders_unparalleled']
    assert (args.broker, args.modules) == ('main:method', ['actors.module'])
    assert args.queues == ['mail', 'orders_unparalleled']
    # a restart by --watch keeps the binding
    assert calls['argv'] == ['tokeo', 'dramatiq', 'serve', '--queues', 'mail', 'orders_unparalleled']


def test_serve_queues_needs_a_name(serve, capsys):
    """--queues without a name exits with an error."""
    with pytest.raises(SystemExit):
        serve('--queues')
    assert '--queues' in capsys.readouterr().err


# ---- worker poll interval -----------------------------------------------------


def test_serve_poll_interval_default_keeps_env(serve):
    """Without config serve leaves dramatiq_worker_timeout alone."""
    serve()
    # without config tokeo leaves the env of dramatiq alone
    assert 'dramatiq_worker_timeout' not in os.environ


def test_serve_poll_interval_from_config(serve, monkeypatch):
    """worker_poll_interval wins over the env, even over an invalid one."""
    monkeypatch.setenv('dramatiq_worker_timeout', '0')
    serve(config=dict(worker_poll_interval=250))
    assert os.environ['dramatiq_worker_timeout'] == '250'


def test_serve_poll_interval_valid_env(serve, monkeypatch):
    """A valid dramatiq_worker_timeout from outside is kept."""
    monkeypatch.setenv('dramatiq_worker_timeout', '500')
    calls = serve()
    assert calls['main']
    assert os.environ['dramatiq_worker_timeout'] == '500'


@pytest.mark.parametrize('value', ['0', '-5', '1s'])
def test_serve_poll_interval_invalid_env(serve, monkeypatch, value):
    """An invalid dramatiq_worker_timeout raises the config error."""
    monkeypatch.setenv('dramatiq_worker_timeout', value)
    with pytest.raises(TokeoDramatiqConfigError, match='Invalid env dramatiq_worker_timeout'):
        serve()
    assert sys.argv == ['tokeo']


@pytest.mark.parametrize('value', [0, 'fast', True])
def test_serve_poll_interval_invalid_config(serve, value):
    """An invalid worker_poll_interval raises the config error."""
    with pytest.raises(TokeoDramatiqConfigError, match='Invalid dramatiq config worker_poll_interval'):
        serve(config=dict(worker_poll_interval=value))
