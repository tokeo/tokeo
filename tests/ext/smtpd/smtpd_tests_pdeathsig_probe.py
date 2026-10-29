"""
Subprocess scenario for the parent-death signal tests.

Started by test_pdeathsig.py so the parent-death signal is observed from
outside a real master process instead of forking the test runner.

```<marker-dir> <count>```: forks ```count``` workers the way the smtpd
extension does (```_set_pdeathsig``` right after the fork), and the master
then only waits. Each worker writes a ```pid_<pid>``` marker on start and a
```stopped_<pid>``` marker when its SIGTERM arrives, here delivered by the
parent-death signal after the master is killed.
"""

# test rules exception: a probe of an extension helper, it runs without a test app

import os
import signal
import sys
import time

from tokeo.ext.smtpd import _set_pdeathsig


def _worker(marker_dir):
    def _stop(*_):
        with open(os.path.join(marker_dir, f'stopped_{os.getpid()}'), 'w') as f:
            f.write('x')
        os._exit(0)

    # arm the handler BEFORE announcing readiness: the pid marker means
    # "a SIGTERM from now on will be recorded"
    signal.signal(signal.SIGTERM, _stop)
    with open(os.path.join(marker_dir, f'pid_{os.getpid()}'), 'w') as f:
        f.write('run')
    while True:
        time.sleep(0.02)


def main():
    marker_dir, count = sys.argv[1], int(sys.argv[2])
    for _ in range(count):
        if os.fork() == 0:
            _set_pdeathsig(signal.SIGTERM)
            try:
                _worker(marker_dir)
            finally:
                os._exit(0)
    # the master only waits until the test kills it
    while True:
        time.sleep(0.1)


if __name__ == '__main__':
    main()
