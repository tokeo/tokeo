"""
Tests for tokeo.core.smtpd.prefork (the midi master/worker process model).

Split in two: platform-neutral tests that run wherever ``os.fork`` exists
(the pre-fork threshold, single-process passthrough, forking, signal-forward
and reaping), and Linux-specialised tests for the parent-death signal
(``PR_SET_PDEATHSIG``) that lets a worker stop itself when the master crashes.

Every signal is delivered to a forked child, never to the pytest process, so a
timing slip can never take the test runner down.
"""

import os
import sys
import time
import signal
import ctypes

import pytest

from tokeo.core.smtpd.prefork import (
    set_pdeathsig,
    is_prefork,
    supervise_workers,
    run_prefork,
)


LINUX = sys.platform.startswith('linux')
HAS_FORK = hasattr(os, 'fork')

requires_fork = pytest.mark.skipif(not HAS_FORK, reason='requires os.fork')
requires_linux = pytest.mark.skipif(not LINUX, reason='PR_SET_PDEATHSIG is Linux-only')


def _alive(pid):
    """True while ```pid``` still resolves (a reaped pid raises)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _read_pdeathsig():
    """Read back the armed parent-death signal via ```PR_GET_PDEATHSIG```."""
    out = ctypes.c_int(0)
    ctypes.CDLL(None, use_errno=True).prctl(2, ctypes.byref(out), 0, 0, 0)
    return out.value


def _reap_orphans():
    """Best-effort reap of any worker that reparented to us after a crash test."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


# --------------------------------------------------------------------------
# platform-neutral: threshold, single-process passthrough, off-linux no-op
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    'count, expected',
    [(0, False), (1, False), (2, True), (4, True), ('3', True), ('1', False)],
)
def test_is_prefork_matches_midi_threshold(count, expected):
    # midi: pre_fork? is @pre_fork > 1
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
    # without ever signalling (proves the waitpid reap loop in isolation)
    child_pids = []
    for _ in range(3):
        pid = os.fork()
        if pid == 0:
            time.sleep(0.1)
            os._exit(0)
        child_pids.append(pid)

    before_int = signal.getsignal(signal.SIGINT)
    before_term = signal.getsignal(signal.SIGTERM)
    supervise_workers(child_pids)
    # the previous handlers are restored on return
    assert signal.getsignal(signal.SIGINT) == before_int
    assert signal.getsignal(signal.SIGTERM) == before_term
    # every worker was reaped -> no longer waitable
    for pid in child_pids:
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, 0)


@requires_fork
def test_run_prefork_forks_supervises_and_reaps(tmp_path):
    # a forked master runs the full run_prefork; SIGTERM to it must forward to
    # every worker, reap them, and let the master exit cleanly
    master = os.fork()
    if master == 0:
        def worker():
            (tmp_path / f'pid_{os.getpid()}').write_text('run')
            signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
            while True:
                time.sleep(0.02)
        run_prefork(worker, count=3)
        os._exit(0)

    # wait until all three workers have announced themselves
    deadline = time.time() + 3.0
    while time.time() < deadline and len(list(tmp_path.glob('pid_*'))) < 3:
        time.sleep(0.02)
    worker_pids = [int(p.name[4:]) for p in tmp_path.glob('pid_*')]
    assert len(worker_pids) == 3

    os.kill(master, signal.SIGTERM)
    _, status = os.waitpid(master, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

    # the master reaped the workers before exiting -> they are gone
    for pid in worker_pids:
        assert not _alive(pid)


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
    master = os.fork()
    if master == 0:
        def worker():
            marker = tmp_path / f'stopped_{os.getpid()}'
            signal.signal(signal.SIGTERM, lambda *_: (marker.write_text('x'), os._exit(0)))
            while True:
                time.sleep(0.02)
        run_prefork(worker, count=2)
        os._exit(0)

    time.sleep(0.5)  # let the master fork the workers and arm pdeathsig
    os.kill(master, signal.SIGKILL)
    os.waitpid(master, 0)

    deadline = time.time() + 3.0
    while time.time() < deadline and not list(tmp_path.glob('stopped_*')):
        time.sleep(0.05)
    _reap_orphans()
    # at least one worker observed the parent's death and stopped itself
    assert list(tmp_path.glob('stopped_*'))
