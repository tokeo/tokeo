"""
Tests for the parent-death signal of a smtpd worker (tokeo.ext.smtpd).

Off Linux ```_set_pdeathsig``` does nothing; on Linux it arms
```PR_SET_PDEATHSIG```, and a worker forked the way the smtpd extension forks
its workers ends by itself when the master crashes.

The crash scenario runs in a subprocess
(```smtpd_tests_pdeathsig_probe.py```) and is observed from outside,
by markers on disk and the process group, so the test runner itself never
forks and never receives a signal.
"""

import os
import sys
import signal
import ctypes
import subprocess

import pytest

from tokeo.ext.smtpd import _set_pdeathsig
from tests.utils import ROOT, wait_until, wait_process_group_ended

LINUX = sys.platform.startswith('linux')

needs_pdeathsig = pytest.mark.skipif(not LINUX, reason='PR_SET_PDEATHSIG is Linux-only')


def _read_pdeathsig():
    """Read back the armed parent-death signal via ```PR_GET_PDEATHSIG```."""
    out = ctypes.c_int(0)
    ctypes.CDLL(None, use_errno=True).prctl(2, ctypes.byref(out), 0, 0, 0)
    return out.value


PROBE = 'tests.ext.smtpd.smtpd_tests_pdeathsig_probe'


def _spawn(*args):
    """Run a probe scenario as its own process group."""
    return subprocess.Popen(
        [sys.executable, '-m', PROBE, *[str(arg) for arg in args]],
        start_new_session=True,
        env=dict(os.environ),
        cwd=ROOT,
    )


# --------------------------------------------------------------------------
# platform-neutral: off-linux no-op
# --------------------------------------------------------------------------


def test_set_pdeathsig_is_noop_off_linux(monkeypatch):
    """Off Linux _set_pdeathsig does nothing and returns False."""
    monkeypatch.setattr(sys, 'platform', 'darwin')
    assert _set_pdeathsig(signal.SIGTERM) is False


def test_set_pdeathsig_returns_false_without_prctl(monkeypatch):
    """A libc without a usable prctl makes _set_pdeathsig return False."""
    monkeypatch.setattr(sys, 'platform', 'linux')

    def no_libc(*args, **kwargs):
        raise OSError('no libc')

    monkeypatch.setattr(ctypes, 'CDLL', no_libc)
    assert _set_pdeathsig(signal.SIGTERM) is False


# --------------------------------------------------------------------------
# Linux-specialised: PR_SET_PDEATHSIG
# --------------------------------------------------------------------------


@needs_pdeathsig
def test_set_pdeathsig_arms_parent_death_signal():
    """_set_pdeathsig arms SIGTERM as the parent death signal."""
    assert _set_pdeathsig(signal.SIGTERM) is True
    try:
        assert _read_pdeathsig() == int(signal.SIGTERM)
    finally:
        ctypes.CDLL(None).prctl(1, 0, 0, 0, 0)  # clear again for the runner


@needs_pdeathsig
def test_set_pdeathsig_honours_requested_signal():
    """_set_pdeathsig arms the signal asked for, here SIGINT."""
    assert _set_pdeathsig(signal.SIGINT) is True
    try:
        assert _read_pdeathsig() == int(signal.SIGINT)
    finally:
        ctypes.CDLL(None).prctl(1, 0, 0, 0, 0)


@needs_pdeathsig
def test_worker_self_terminates_when_master_crashes(tmp_path):
    """
    The master of two workers dies by SIGKILL; each worker stops itself by
    the parent death signal and the group ends.
    """
    proc = _spawn(tmp_path, 2)
    try:
        assert wait_until(lambda: len(list(tmp_path.glob('pid_*'))) == 2, 3.0)
        proc.kill()
        proc.wait(timeout=8)
        # every worker observed the parent death and the group is gone
        assert wait_until(lambda: len(list(tmp_path.glob('stopped_*'))) == 2, 3.0)
        assert wait_process_group_ended(proc.pid)
    finally:
        proc.kill()
