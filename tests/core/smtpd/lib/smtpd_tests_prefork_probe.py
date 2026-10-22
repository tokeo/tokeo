"""
Subprocess scenarios for the prefork tests.

Started by test_prefork.py so the fork/supervise/pdeathsig behaviour is
observed from outside a real master process instead of forking the test
runner. Two scenarios:

``supervise``: forks three short-lived sleepers, runs ``supervise_workers``
and asserts in-process that the signal handlers were restored and every
worker was reaped -- the exit code carries the verdict.

``run_prefork <marker-dir> <count>``: runs ``run_prefork`` with workers that
write a ``pid_<pid>`` marker on start and a ``stopped_<pid>`` marker when
their SIGTERM arrives (forwarded by the master, or delivered by the
parent-death signal after a master crash).
"""

import os
import signal
import sys
import time

from tokeo.core.smtpd.prefork import run_prefork, supervise_workers


def scenario_supervise():
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
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            continue
        raise AssertionError(f'worker {pid} was not reaped')


def scenario_run_prefork(marker_dir, count):
    def worker():
        def stop(*_):
            with open(os.path.join(marker_dir, f'stopped_{os.getpid()}'), 'w') as f:
                f.write('x')
            os._exit(0)

        # arm the handler BEFORE announcing readiness: the pid marker means
        # "a SIGTERM from now on will be recorded"
        signal.signal(signal.SIGTERM, stop)
        with open(os.path.join(marker_dir, f'pid_{os.getpid()}'), 'w') as f:
            f.write('run')
        while True:
            time.sleep(0.02)

    run_prefork(worker, count=count)


def main():
    if sys.argv[1] == 'supervise':
        scenario_supervise()
    else:
        scenario_run_prefork(sys.argv[2], int(sys.argv[3]))


if __name__ == '__main__':
    main()
