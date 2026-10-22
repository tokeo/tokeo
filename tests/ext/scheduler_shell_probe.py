"""
A tokeo app with the scheduler shell, run by test_ext_scheduler in a pseudo
terminal.

Its task ping logs by app.log; the shell fires it by ```tasks fire 1```.
"""

import sys
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.ext.appshare import app


class ProbeApp(TokeoTest):

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
    config = init_defaults('scheduler')
    config['scheduler']['tasks'] = {'ping': dict(module='tests.ext.scheduler_shell_probe', crontab='0 0 1 1 *')}
    with ProbeApp(argv=sys.argv[1:], config_defaults=config) as probe:
        probe.run()
