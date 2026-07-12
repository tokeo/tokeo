"""
Tests for tokeo.core.smtpd.prefork (the master/worker process model).

Split in two: platform-neutral tests that run wherever ``os.fork`` exists
(the pre-fork threshold, single-process passthrough, forking, signal-forward
and reaping), and Linux-specialised tests for the parent-death signal
(``PR_SET_PDEATHSIG``) that lets a worker stop itself when the master crashes.

The process scenarios run in a subprocess (``lib/prefork_helper.py``) and are
observed from outside -- markers on disk, exit codes and the process group --
so the test runner itself never forks and never receives a signal.
"""

import os
import sys
import time
import signal
import ctypes
import subprocess

import pytest

from tokeo.core.smtpd.prefork import (
    set_pdeathsig,
    is_prefork,
    run_prefork,
)


LINUX = sys.platform.startswith('linux')
HAS_FORK = hasattr(os, 'fork')

requires_fork = pytest.mark.skipif(not HAS_FORK, reason='requires os.fork')
requires_linux = pytest.mark.skipif(not LINUX, reason='PR_SET_PDEATHSIG is Linux-only')


def _read_pdeathsig():
    """Read back the armed parent-death signal via ```PR_GET_PDEATHSIG```."""
    out = ctypes.c_int(0)
    ctypes.CDLL(None, use_errno=True).prctl(2, ctypes.byref(out), 0, 0, 0)
    return out.value


HELPER = os.path.join(os.path.dirname(__file__), 'lib', 'prefork_helper.py')


def _spawn(*args):
    """Run a helper scenario as its own process group."""
    return subprocess.Popen(
        [sys.executable, HELPER, *[str(arg) for arg in args]],
        start_new_session=True,
        env=dict(os.environ),
    )


def _assert_group_gone(pgid, timeout=5.0):
    """The whole process group of the scenario has ended."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f'process group {pgid} still has members')


# --------------------------------------------------------------------------
# platform-neutral: threshold, single-process passthrough, off-linux no-op
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    'count, expected',
    [(0, False), (1, False), (2, True), (4, True), ('3', True), ('1', False)],
)
def test_is_prefork_matches_reference_threshold(count, expected):
    # Ruby: pre_fork? is @pre_fork > 1
    assert is_prefork(count) is expected


def test_set_pdeathsig_is_noop_off_linux(monkeypatch):
    # must never raise and reports False when not on Linux
    monkeypatch.setattr(sys, 'platform', 'darwin')
    assert set_pdeathsig(signal.SIGTERM) is False


def test_run_prefork_single_process_runs_worker_in_place():
    # count <= 1 -> no fork; worker runs in this very process, returns no pids
    calls = []
    result = run_prefork(lambda: calls.append(os.getpid()), count=1)
    assert result == []
    assert calls == [os.getpid()]


# --------------------------------------------------------------------------
# platform-neutral (needs fork): forking, signal-forward, reaping
# --------------------------------------------------------------------------


@requires_fork
def test_supervise_workers_reaps_and_restores_handlers():
    # workers that exit on their own -> supervise reaps them all and returns,
    # without ever signalling; the helper asserts handler restore and reaping
    # in-process, its exit code carries the verdict
    proc = _spawn('supervise')
    assert proc.wait(timeout=8) == 0
    _assert_group_gone(proc.pid)


@requires_fork
def test_run_prefork_forks_supervises_and_reaps(tmp_path):
    # a master subprocess runs the full run_prefork; SIGTERM to it must
    # forward to every worker, reap them, and let the master exit cleanly
    proc = _spawn('run_prefork', tmp_path, 3)
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline and len(list(tmp_path.glob('pid_*'))) < 3:
            time.sleep(0.02)
        assert len(list(tmp_path.glob('pid_*'))) == 3
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=8) == 0
        # every worker saw the forwarded SIGTERM and the group is gone
        assert len(list(tmp_path.glob('stopped_*'))) == 3
        _assert_group_gone(proc.pid)
    finally:
        proc.kill()


# --------------------------------------------------------------------------
# Linux-specialised: PR_SET_PDEATHSIG
# --------------------------------------------------------------------------


@requires_linux
def test_set_pdeathsig_arms_parent_death_signal():
    assert set_pdeathsig(signal.SIGTERM) is True
    try:
        assert _read_pdeathsig() == int(signal.SIGTERM)
    finally:
        ctypes.CDLL(None).prctl(1, 0, 0, 0, 0)  # clear again for the runner


@requires_linux
def test_set_pdeathsig_honours_requested_signal():
    assert set_pdeathsig(signal.SIGINT) is True
    try:
        assert _read_pdeathsig() == int(signal.SIGINT)
    finally:
        ctypes.CDLL(None).prctl(1, 0, 0, 0, 0)


@requires_linux
def test_worker_self_terminates_when_master_crashes(tmp_path):
    # a hard master crash (SIGKILL) cannot forward anything; each worker must
    # still stop itself through the armed parent-death signal
    proc = _spawn('run_prefork', tmp_path, 2)
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline and len(list(tmp_path.glob('pid_*'))) < 2:
            time.sleep(0.02)
        proc.kill()
        proc.wait(timeout=8)
        deadline = time.time() + 3.0
        while time.time() < deadline and len(list(tmp_path.glob('stopped_*'))) < 2:
            time.sleep(0.05)
        # every worker observed the parent death and the group is gone
        assert len(list(tmp_path.glob('stopped_*'))) == 2
        _assert_group_gone(proc.pid)
    finally:
        proc.kill()
