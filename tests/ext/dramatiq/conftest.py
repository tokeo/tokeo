"""
Shared fixtures for the tests of tokeo.ext.dramatiq.

The builders create a test app with the dramatiq extension and a diskcache
in the temporary directory; the fixtures hand them to the tests.
"""

import pytest
from cement.utils.misc import init_defaults
from dramatiq.brokers import rabbitmq as dramatiq_rabbitmq
from tokeo.main import TokeoTest


class DramatiqTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.diskcache',
            'tokeo.ext.tui.output',
            'tokeo.ext.dramatiq',
        ]


def _dramatiq_app(tmp, argv):
    d = init_defaults('diskcache')
    d['diskcache']['directory'] = tmp.dir
    return DramatiqTest(argv=argv, config_defaults=d)


def _broker_app(tmp, rabbitmq):
    d = init_defaults('diskcache', 'dramatiq')
    d['diskcache']['directory'] = tmp.dir
    if rabbitmq is not None:
        d['dramatiq']['rabbitmq'] = rabbitmq
    return DramatiqTest(argv=[], config_defaults=d)


def _middleware_app(tmp, **config):
    d = init_defaults('diskcache', 'dramatiq')
    d['diskcache']['directory'] = tmp.dir
    d['dramatiq'].update(config)
    return DramatiqTest(argv=[], config_defaults=d)


@pytest.fixture
def keep_dead_message_ttl(monkeypatch):
    # register sets the dramatiq constant, restore it after the test
    monkeypatch.setattr(dramatiq_rabbitmq, 'DEAD_MESSAGE_TTL', dramatiq_rabbitmq.DEAD_MESSAGE_TTL)
    return dramatiq_rabbitmq.DEAD_MESSAGE_TTL


@pytest.fixture
def dramatiq_app():
    """Builder of a test app, called with the same arguments as before."""
    return _dramatiq_app


@pytest.fixture
def broker_app():
    """Builder of a test app, called with the same arguments as before."""
    return _broker_app


@pytest.fixture
def middleware_app():
    """Builder of a test app, called with the same arguments as before."""
    return _middleware_app
