"""
Shared parts of the tests of tokeo.ext.dramatiq: the test apps.

Each test app keeps its diskcache in the temporary directory of the test.
"""

from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest


class DramatiqTest(TokeoTest):
    """A test app with the dramatiq extension, run by argv."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.diskcache',
            'tokeo.ext.tui.output',
            'tokeo.ext.dramatiq',
        ]

    def __init__(self, tmp, argv=None, **dramatiq):
        config = init_defaults('diskcache', 'dramatiq')
        config['diskcache']['directory'] = tmp.dir
        config['dramatiq'].update(dramatiq)
        super().__init__(argv=argv or [], config_defaults=config)


class DramatiqBrokerTest(DramatiqTest):
    """A test app with the given rabbitmq settings of the broker."""

    def __init__(self, tmp, rabbitmq=None):
        super().__init__(tmp, **({} if rabbitmq is None else dict(rabbitmq=rabbitmq)))


class DramatiqMiddlewareTest(DramatiqTest):
    """A test app with the given dramatiq settings, e.g. of the middleware."""

    def __init__(self, tmp, argv=None, **config):
        super().__init__(tmp, argv, **config)
