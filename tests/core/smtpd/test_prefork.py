"""
Tests for tokeo.core.smtpd.prefork (the master/worker process model).

Split in two: platform-neutral tests that run wherever ``os.fork`` exists
(the pre-fork threshold, single-process passthrough, forking, signal-forward
and reaping), and Linux-specialised tests for the parent-death signal
(``PR_SET_PDEATHSIG``) that lets a worker stop itself when the master crashes.

The process scenarios run in a subprocess
(``lib/smtpd_tests_prefork_probe.py``) and are observed from outside --
markers on disk, exit codes and the process group -- so the test runner
itself never forks and never receives a signal.
"""

import os
import sys
import signal
import ctypes
import subprocess

import pytest

from tokeo.core.smtpd.prefork import (
    set_pdeathsig,
    is_prefork,
    run_prefork,
)
from tests.utils import ROOT, wait_until, wait_process_group_ended

LINUX = sys.platform.startswith('linux')
HAS_FORK = hasattr(os, 'fork')

requires_fork = pytest.mark.skipif(not HAS_FORK, reason='requires os.fork')
requires_linux = pytest.mark.skipif(not LINUX, reason='PR_SET_PDEATHSIG is Linux-only')


def _read_pdeathsig():
    """Read back the armed parent-death signal via ```PR_GET_PDEATHSIG```."""
    out = ctypes.c_int(0)
    ctypes.CDLL(None, use_errno=True).prctl(2, ctypes.byref(out), 0, 0, 0)
    return out.value


PROBE = 'tests.core.smtpd.lib.smtpd_tests_prefork_probe'


def _spawn(*args):
    """Run a probe scenario as its own process group."""
    return subprocess.Popen(
        [sys.executable, '-m', PROBE, *[str(arg) for arg in args]],
        start_new_session=True,
        env=dict(os.environ),
        cwd=ROOT,
    )


# --------------------------------------------------------------------------
# platform-neutral: threshold, single-process passthrough, off-linux no-op
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    'count, expected',
    [(0, False), (1, False), (2, True), (4, True), ('3', True), ('1', False)],
)
def test_is_prefork_matches_reference_threshold(count, expected):
    """pre_fork means more than one process, as in the reference."""
    assert is_prefork(count) is expected


def test_set_pdeathsig_is_noop_off_linux(monkeypatch):
    """Off Linux set_pdeathsig does nothing and returns False."""
    monkeypatch.setattr(sys, 'platform', 'darwin')
    assert set_pdeathsig(signal.SIGTERM) is False


def test_run_prefork_single_process_runs_worker_in_place():
    """With count 1 the worker runs in this process, no pid comes back."""
    calls = []
    result = run_prefork(lambda: calls.append(os.getpid()), count=1)
    assert result == []
    assert calls == [os.getpid()]


# --------------------------------------------------------------------------
# platform-neutral (needs fork): forking, signal-forward, reaping
# --------------------------------------------------------------------------


@requires_fork
def test_supervise_workers_reaps_and_restores_handlers():
    """
    The probe forks three workers that end on their own; supervise reaps
    them and restores the signal handlers, the exit code 0 tells it.
    """
    proc = _spawn('supervise')
    assert proc.wait(timeout=8) == 0
    assert wait_process_group_ended(proc.pid)


@requires_fork
def test_run_prefork_forks_supervises_and_reaps(tmp_path):
    """
    The probe runs run_prefork with three workers; a SIGTERM to the master
    reaches every worker, the master exits 0 and the group ends.
    """
    proc = _spawn('run_prefork', tmp_path, 3)
    try:
        assert wait_until(lambda: len(list(tmp_path.glob('pid_*'))) == 3, 3.0)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=8) == 0
        # every worker saw the forwarded SIGTERM and the group is gone
        assert len(list(tmp_path.glob('stopped_*'))) == 3
        assert wait_process_group_ended(proc.pid)
    finally:
        proc.kill()


# --------------------------------------------------------------------------
# Linux-specialised: PR_SET_PDEATHSIG
# --------------------------------------------------------------------------


@requires_linux
def test_set_pdeathsig_arms_parent_death_signal():
    """set_pdeathsig arms SIGTERM as the parent death signal."""
    assert set_pdeathsig(signal.SIGTERM) is True
    try:
        assert _read_pdeathsig() == int(signal.SIGTERM)
    finally:
        ctypes.CDLL(None).prctl(1, 0, 0, 0, 0)  # clear again for the runner


@requires_linux
def test_set_pdeathsig_honours_requested_signal():
    """set_pdeathsig arms the signal asked for, here SIGINT."""
    assert set_pdeathsig(signal.SIGINT) is True
    try:
        assert _read_pdeathsig() == int(signal.SIGINT)
    finally:
        ctypes.CDLL(None).prctl(1, 0, 0, 0, 0)


@requires_linux
def test_worker_self_terminates_when_master_crashes(tmp_path):
    """
    The master of two workers dies by SIGKILL; each worker stops itself by
    the parent death signal and the group ends.
    """
    proc = _spawn('run_prefork', tmp_path, 2)
    try:
        assert wait_until(lambda: len(list(tmp_path.glob('pid_*'))) == 2, 3.0)
        proc.kill()
        proc.wait(timeout=8)
        # every worker observed the parent death and the group is gone
        assert wait_until(lambda: len(list(tmp_path.glob('stopped_*'))) == 2, 3.0)
        assert wait_process_group_ended(proc.pid)
    finally:
        proc.kill()
