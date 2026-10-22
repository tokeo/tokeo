"""
Shared parts of the tests of tokeo.ext.scheduler.
"""

from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest


class SchedulerTest(TokeoTest):
    """A test app with the scheduler and the given tasks."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
            'tokeo.ext.tui.output',
            'tokeo.ext.scheduler',
        ]

    def __init__(self, tasks=None, **kw):
        # time.time as task by default, it is never due during a test
        config = init_defaults('scheduler')
        config['scheduler']['tasks'] = tasks or {'time': dict(module='time', crontab='0 0 1 1 *', name='the time')}
        super().__init__(config_defaults=config, **kw)
