"""
Tests for start_service of tokeo.core.cement.service: the name of the process,
the start and the end line, the crash file and the thread dump by the config of
the app, and the settings it refuses.

A probe that starts a service runs in a process of its own, start_service
changes the process it runs in.
"""

import sys
import subprocess

import pytest

from tokeo.main import TokeoTest
from tokeo.core.cement.service import start_service, TokeoServiceError
from tests.utils import ROOT_DIR

# a probe app in debug mode with the config of the app section from argv[1]
_PROBE = (
    'import os, sys, json, signal\n'
    'from tokeo.main import TokeoTest\n'
    'from tokeo.core.cement.service import start_service\n'
    "with TokeoTest(argv=['--debug'], config_defaults={'tokeo_test': json.loads(sys.argv[1])}) as app:\n"
)


def _run_probe(body, settings='{}'):
    # run the probe app with the body inside its with block
    code = _PROBE + ''.join(f'    {line}\n' for line in body)
    return subprocess.run([sys.executable, '-c', code, settings], cwd=ROOT_DIR, capture_output=True, text=True, timeout=30)


def test_start_service_names_the_process_and_logs_start_and_end():
    """The kernel gets the short name of worker 3, the log lines the long name."""
    done_process = _run_probe(["start_service(app, 'probe-worker', 'prb', 3)", "print(open('/proc/self/comm').read().strip())"])
    if sys.platform.startswith('linux'):
        assert done_process.stdout.splitlines() == ['tokeo_-prb3']
    assert 'service tokeo_test-probe-worker-3 started (pid ' in done_process.stderr
    assert 'service tokeo_test-probe-worker-3 ended normally after ' in done_process.stderr
    assert '(exit code 0)' in done_process.stderr


def test_start_service_tells_the_signal_that_ended_it():
    """A caught SIGTERM is the reason of the end line."""
    # cement runs the hook signal like this before it raises CaughtSignal; the
    # run is a generator, list() runs it
    done_process = _run_probe(["start_service(app, 'probe', 'prb')", "list(app.hook.run('signal', app, signal.SIGTERM, None))"])
    assert 'service tokeo_test-probe ended by SIGTERM after ' in done_process.stderr


def test_start_service_writes_a_thread_dump_into_the_crash_file(tmp_path):
    """With crash_dir and SIGUSR1 the signal writes the stacks to the crash file."""
    settings = f'{{"crash_dir": "{tmp_path}", "thread_dump_signal": "SIGUSR1"}}'
    done_process = _run_probe(["start_service(app, 'probe', 'prb')", 'os.kill(os.getpid(), signal.SIGUSR1)'], settings)
    assert done_process.returncode == 0, done_process.stderr
    assert 'Current thread' in (tmp_path / 'tokeo_test-probe.crash').read_text()


@pytest.mark.parametrize(
    'settings, expected',
    [
        (dict(crash_dir=''), "tokeo_test.crash_dir must be a directory, not ''"),
        (dict(crash_dir=7), 'tokeo_test.crash_dir must be a directory, not 7'),
        (dict(thread_dump_signal='SIGNOPE'), "tokeo_test.thread_dump_signal must name a signal like SIGUSR1, not 'SIGNOPE'"),
        (dict(thread_dump_signal='SIG_DFL'), "tokeo_test.thread_dump_signal must name a signal like SIGUSR1, not 'SIG_DFL'"),
        (dict(thread_dump_signal=10), 'tokeo_test.thread_dump_signal must name a signal like SIGUSR1, not 10'),
    ],
    ids=['dir_empty', 'dir_number', 'signal_unknown', 'signal_handler', 'signal_number'],
)
def test_start_service_refuses_a_wrong_setting(settings, expected):
    """A wrong crash_dir or thread_dump_signal raises before the process changes."""
    with TokeoTest(config_defaults={'tokeo_test': settings}) as app:
        with pytest.raises(TokeoServiceError) as err_info:
            start_service(app, 'probe', 'prb')
    assert str(err_info.value) == expected
