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
from tokeo.main import TokeoTest
from tokeo.ext.dramatiq import TokeoDramatiqError


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
    for name in ('dramatiq_restart_delay', 'dramatiq_queue_prefetch', 'dramatiq_delay_queue_prefetch'):
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

    def run(*options, config=None):
        with dramatiq_app(tmp, ['dramatiq', 'serve', *options]) as app:
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
