"""
A tokeo app showing dramatiq monitor --follow, run by the monitoring tests in
a pseudo terminal to end it by Ctrl-C.

The monitor gets a fixed snapshot instead of listening to rabbitmq.
"""

import sys
import time
from tokeo.main import Tokeo


class ProbeApp(Tokeo):

    class Meta:
        label = 'probe'
        extensions = [
            'colorlog',
            'tokeo.ext.print',
            'tokeo.ext.tui.output',
            'tokeo.ext.dramatiq',
        ]
        config_files = []


def snapshot(wait=0):
    time.sleep(wait)
    queues = [dict(name='mail', ready=0, delayed=0, dead=0, consumers=1)]
    totals = dict(ready=0, delayed=0, dead=0, consumers=1)
    return dict(at=int(time.time() * 1000), monitoring=True, interval=1, workers=[], actors={}, queues=queues, queue_totals=totals)


if __name__ == '__main__':
    with ProbeApp(argv=sys.argv[1:]) as app:
        app.dramatiq.monitor.snapshot = snapshot
        app.run()
