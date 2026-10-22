"""
A tokeo app showing dramatiq monitor --follow, run by the monitoring tests in
a pseudo terminal to end it by Ctrl-C.

The monitor gets a fixed snapshot instead of listening to rabbitmq.
"""

import sys
import time
import shutil
from cement.utils import fs
from tests.ext.dramatiq.lib.dramatiq_tests_helper import DramatiqTest


class DramatiqMonitorProbeTest(DramatiqTest):

    class Meta:
        label = 'probe'


def snapshot(wait=0):
    time.sleep(wait)
    queues = [dict(name='mail', ready=0, delayed=0, dead=0, consumers=1)]
    totals = dict(ready=0, delayed=0, dead=0, consumers=1)
    return dict(at=int(time.time() * 1000), monitoring=True, interval=1, workers=[], actors={}, queues=queues, queue_totals=totals)


if __name__ == '__main__':
    tmp = fs.Tmp()
    try:
        with DramatiqMonitorProbeTest(tmp, sys.argv[1:]) as app:
            app.dramatiq.monitor.snapshot = snapshot
            app.run()
    finally:
        shutil.rmtree(tmp.dir, ignore_errors=True)
