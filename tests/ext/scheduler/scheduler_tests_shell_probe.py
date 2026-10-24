"""
A tokeo app with the scheduler shell, run by test_scheduler in a pseudo
terminal.

Its task ping logs by app.log; the shell fires it by ```tasks fire 1```.
"""

import sys
from tokeo.ext.appshare import app
from tests.ext.scheduler.scheduler_tests_helper import SchedulerTest


class SchedulerShellProbeTest(SchedulerTest):

    class Meta:
        label = 'probe'
        extensions = [
            'tokeo.ext.appshare',
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
            'tokeo.ext.tui.output',
            'tokeo.ext.scheduler',
        ]


def ping():
    app.log.warning('MARK-PING')


if __name__ == '__main__':
    tasks = {'ping': dict(module='tests.ext.scheduler.scheduler_tests_shell_probe', crontab='0 0 1 1 *')}
    with SchedulerShellProbeTest(tasks, argv=sys.argv[1:]) as probe:
        probe.run()
