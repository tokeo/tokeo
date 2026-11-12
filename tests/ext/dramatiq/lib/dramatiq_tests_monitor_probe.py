"""
A tokeo app showing dramatiq monitor --follow, run by the monitoring tests in
a pseudo terminal to end it by Ctrl-C.

The monitor gets a fixed snapshot instead of listening to rabbitmq.
"""

import sys
import time
from tests.utils import Tmp
from tests.ext.dramatiq.lib.dramatiq_tests_helper import DramatiqTest


class DramatiqMonitorProbeTest(DramatiqTest):

    class Meta:
        label = 'probe'


def snapshot(wait_seconds=0):
    time.sleep(wait_seconds)
    queues = [dict(name='mail', ready_cnt=0, delayed_cnt=0, dead_cnt=0, consumer_cnt=1)]
    totals = dict(ready_cnt=0, delayed_cnt=0, dead_cnt=0, consumer_cnt=1)
    return dict(at=int(time.time() * 1000), monitoring=True, interval_seconds=1, workers=[], actors={}, queues=queues, queue_totals=totals)


if __name__ == '__main__':
    # the first argument is the directory of the test, its tmp stays there
    tmp = Tmp(dir=sys.argv[1])
    with DramatiqMonitorProbeTest(tmp, sys.argv[2:]) as app:
        app.dramatiq.monitor.snapshot = snapshot
        app.run()
