"""
Tests for the smtpd extension: settings mapping, topology planning, shared
limits across processes and the termination guarantees with real processes.

The termination guarantees are process properties (exit code, port release,
SIGKILL survival) and are observed from outside: ``smtpd_helper_app.py``
runs a real ``TokeoTest`` Cement app in its own process group; the tests
detect readiness through the SMTP banner on the port and cleanup through
the process group and the port becoming bindable again.
"""

import asyncio
import multiprocessing
import os
import signal
import smtplib
import socket
import subprocess
import sys
import time

import pytest

from tokeo.core.smtpd import GlobalLimits
from tokeo.ext.smtpd import (
    TokeoSmtpdError,
    SharedGlobalLimits,
    _resolve_events_handler,
    _plan,
)


# --- unit: handler resolving -----------------------------------------------------


def test_resolve_events_handler_imports_and_rejects():
    from tokeo.ext.smtpd import TokeoSmtpdEvents

    assert _resolve_events_handler('tokeo.ext.smtpd.TokeoSmtpdEvents') is TokeoSmtpdEvents
    with pytest.raises(TokeoSmtpdError):
        _resolve_events_handler('NoDots')
    with pytest.raises(TokeoSmtpdError):
        _resolve_events_handler('tokeo.core.smtpd.events.NoSuchClass')
    with pytest.raises(TokeoSmtpdError):
        # importable, but not a TokeoSmtpdEvents subclass: the core class
        _resolve_events_handler('tokeo.core.smtpd.events.SmtpdEvents')


def test_events_handler_carries_the_app():
    from tokeo.ext.smtpd import TokeoSmtpdEvents

    events_handler = TokeoSmtpdEvents('app-sentinel', options={'answer': 42})
    assert events_handler.app == 'app-sentinel'
    assert events_handler.options == {'answer': 42}


# --- unit: topology planning ----------------------------------------------------


def _svc(name, pre_fork=0):
    svc = {'name': name, 'settings': {}}
    if pre_fork:
        svc['settings']['pre_fork'] = pre_fork
    return svc


def test_plan_single_service_stays_in_main_process():
    # pre_fork <= 1 with one service: no fork, no shared limits
    for pre_fork in (0, 1):
        plan = _plan([_svc('mx1', pre_fork)], has_global_limit=True)
        assert plan['inloop'] == [_svc('mx1', pre_fork)]
        assert plan['forks'] == [] and plan['shared'] is False


def test_plan_single_service_prefork_main_is_worker_one():
    plan = _plan([_svc('mx1', 3)], has_global_limit=True)
    assert plan['inloop'][0]['name'] == 'mx1'          # main process serves
    assert plan['forks'] == [(_svc('mx1', 3), 2)]      # plus N-1 workers
    assert plan['reuse'] == {'mx1'}
    assert plan['shared'] is True


def test_plan_multi_services_rules():
    services = [_svc('mx1', 5), _svc('mx2'), _svc('mx3', 1)]
    plan = _plan(services, has_global_limit=True)
    assert [svc['name'] for svc in plan['inloop']] == ['mx2']
    assert [(svc['name'], count) for svc, count in plan['forks']] == [('mx1', 5), ('mx3', 1)]
    assert plan['reuse'] == {'mx1'}                    # only groups > 1 share a port
    assert plan['shared'] is True
    # all services forked: the master only supervises
    plan = _plan([_svc('mx1', 2), _svc('mx2', 1)], has_global_limit=False)
    assert plan['inloop'] == [] and plan['shared'] is False


# --- unit: shared limits ---------------------------------------------------------


def test_shared_limits_count_across_processes():
    limits = SharedGlobalLimits(max_processings=5, max_connections=7)
    assert isinstance(limits, GlobalLimits)
    assert limits.max_processings == 5 and limits.max_connections == 7

    def child(shared):
        shared.admit()
        shared.admit()
        shared.begin_processing()

    # the extension inherits the counters via os.fork; mirror that in the
    # test (the spawn default on macOS would also have to pickle 'child')
    proc = multiprocessing.get_context('fork').Process(target=child, args=(limits,))
    proc.start()
    proc.join(5)
    assert limits.active == 2 and limits.active_processings == 1
    limits.release()
    limits.end_processing()
    limits.release()
    limits.release()                                    # floor at 0
    assert limits.active == 0 and limits.active_processings == 0


def test_shared_limits_slot_wait_recheck_interval():
    limits = SharedGlobalLimits(max_processings=1)

    async def measure():
        limits.slot_free.clear()
        start = time.perf_counter()
        await limits.slot_wait()                        # nobody sets the event
        return time.perf_counter() - start

    assert asyncio.run(measure()) < 0.5                 # returns via the poll


# --- cement wiring ---------------------------------------------------------------


def test_extension_loads_into_cement_app():
    from tokeo.main import TokeoTest

    class SmtpdApp(TokeoTest):

        class Meta:
            extensions = [
                'tokeo.ext.yaml',
                'tokeo.ext.print',
                'tokeo.ext.smtpd',
            ]

    config = dict(smtpd=dict(services=[{
        'name': 'mx-a',
        'events_handler': 'tokeo.ext.smtpd.TokeoSmtpdEvents',
        'listeners': [{'host': '127.0.0.1', 'port': 2525}],
    }]))
    with SmtpdApp(config_defaults=config) as app:
        assert app.smtpd is not None
        assert [svc['name'] for svc in app.smtpd.services()] == ['mx-a']
        with pytest.raises(TokeoSmtpdError):
            app.smtpd.services(['nope'])
        with pytest.raises(TokeoSmtpdError):
            app.smtpd.services(['mx-a', 'mx-a'])   # named twice must not start twice
        app.smtpd.list()   # must run without raising


# --- process tests: real termination behaviour ----------------------------------


HELPER = os.path.join(os.path.dirname(__file__), 'lib/smtpd_helper_app.py')
assert os.path.exists(HELPER), 'smtpd_helper_app.py must live in lib/ next to this test file'


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _service(name, port, pre_fork=0):
    svc = {
        'name': name,
        'events_handler': 'tokeo.ext.smtpd.TokeoSmtpdEvents',
        'listeners': [{'host': '127.0.0.1', 'port': port}],
    }
    if pre_fork:
        svc['settings'] = {'pre_fork': pre_fork}
    return svc


def _spawn(config, names=None):
    import json
    import tempfile

    # an own session = an own process group: master and all its workers,
    # observable from outside via os.killpg; all output goes to a file so a
    # failing helper can report its actual error instead of a blind timeout
    log = tempfile.NamedTemporaryFile(mode='w+', prefix='smtpd-helper-', suffix='.log', delete=False)
    proc = subprocess.Popen(
        [sys.executable, HELPER, json.dumps(config), json.dumps(names)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=dict(os.environ),
    )
    proc.helper_log = log.name
    return proc


def _helper_log(proc, lines=15):
    with open(proc.helper_log) as f:
        tail = f.readlines()[-lines:]
    return ''.join(tail).strip() or '(no output)'


def _wait_banner(proc, port, timeout=8.0):
    """Readiness: the SMTP banner answers on the port."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f'helper exited rc={proc.returncode} before serving:\n{_helper_log(proc)}')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1) as sock:
                if sock.recv(4).startswith(b'220'):
                    return
        except OSError:
            pass
        time.sleep(0.05)
    raise AssertionError(f'no smtp banner on port {port}:\n{_helper_log(proc)}')


def _child_pids(pid):
    """The forked workers of the master (Linux process tree)."""
    with open(f'/proc/{pid}/task/{pid}/children') as f:
        return [int(child) for child in f.read().split()]


def _ehlo_ok(port):
    client = smtplib.SMTP('127.0.0.1', port, timeout=5)
    code, _ = client.ehlo('probe')
    client.quit()
    assert code == 250


def _assert_port_free(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))


def _assert_group_gone(pgid, timeout=5.0):
    """The whole process group of the invocation has ended."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f'process group {pgid} still has members')


def test_term_single_service_in_main_process():
    port = _free_port()
    proc = _spawn({'services': [_service('mx1', port)]}, names=None)
    try:
        _wait_banner(proc, port)
        _ehlo_ok(port)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=8) == 0
        _assert_group_gone(proc.pid)
        _assert_port_free(port)
    finally:
        proc.kill()


def test_term_single_service_prefork_group():
    # pre_fork 3 with one service: main process is worker #1 plus 2 forks
    port = _free_port()
    proc = _spawn({'services': [_service('mx1', port, pre_fork=3)]}, names=None)
    try:
        _wait_banner(proc, port)
        if sys.platform.startswith('linux'):
            assert len(_child_pids(proc.pid)) == 2
        _ehlo_ok(port)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=8) == 0
        _assert_group_gone(proc.pid)
        _assert_port_free(port)
    finally:
        proc.kill()


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='the parent-death signal is Linux-only')
def test_kill_master_takes_workers_down():
    # a SIGKILL on the master cannot forward anything; the workers must end
    # through the parent-death signal
    port = _free_port()
    proc = _spawn({'services': [_service('mx1', port, pre_fork=3)]}, names=None)
    try:
        _wait_banner(proc, port)
        assert len(_child_pids(proc.pid)) == 2
        proc.kill()
        proc.wait(timeout=8)
        _assert_group_gone(proc.pid)
    finally:
        proc.kill()


def test_term_mixed_services_with_shared_limits():
    # mx1 as a 2-worker group, mx2 in the main loop, a global cap forces the
    # shared limits path; one SIGTERM must end the whole invocation
    port_a, port_b = _free_port(), _free_port()
    config = {
        'max_connections': 10,
        'services': [_service('mx1', port_a, pre_fork=2), _service('mx2', port_b)],
    }
    proc = _spawn(config, names=None)
    try:
        _wait_banner(proc, port_a)
        _wait_banner(proc, port_b)
        _ehlo_ok(port_a)
        _ehlo_ok(port_b)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=8) == 0
        _assert_group_gone(proc.pid)
        _assert_port_free(port_a)
        _assert_port_free(port_b)
    finally:
        proc.kill()
