"""
Tests for tokeo.ext.dramatiq.exc, the fields and messages of the errors.
"""

import copy
import pickle
import multiprocessing

import pika
import pytest
from pika.adapters.utils.connection_workflow import AMQPConnectorSocketConnectError
from dramatiq.errors import ConnectionClosed
from tokeo.core.exc import TokeoError
from tokeo.ext.dramatiq import (
    TokeoDramatiqError,
    TokeoDramatiqConfigError,
    TokeoDramatiqConnectionError,
    TokeoDramatiqRefusedError,
    TokeoDramatiqQueueError,
)


@pytest.mark.parametrize(
    'source, name, message',
    [
        ('config', 'max_age', "Invalid dramatiq config max_age: Value 'x' must be null or an integer >= 1!"),
        ('env', 'dramatiq_worker_timeout', "Invalid env dramatiq_worker_timeout: Value 'x' must be null or an integer >= 1!"),
        ('switch', '--limit', "Invalid switch --limit: Value 'x' must be null or an integer >= 1!"),
    ],
)
def test_config_error(source, name, message):
    e = TokeoDramatiqConfigError(source, name, 'x', 'null or an integer >= 1')
    assert str(e) == message and e.args == (message,)
    assert (e.source, e.name, e.value, e.expected) == (source, name, 'x', 'null or an integer >= 1')


def test_connection_error():
    cause = ConnectionClosed('connection refused')
    e = TokeoDramatiqConnectionError('mq.example:5672', cause)
    assert str(e) == 'Cannot connect to rabbitmq at mq.example:5672 (ConnectionClosed: connection refused)!'
    assert (e.address, e.cause, e.actor_name) == ('mq.example:5672', 'ConnectionClosed: connection refused', None)
    assert e.args == (str(e),)
    # sending names the actor, a broker without address is named generic
    e = TokeoDramatiqConnectionError(None, cause, actor_name='count_words')
    assert str(e) == 'Cannot send count_words to the broker (ConnectionClosed: connection refused)!'


def test_refused_error():
    cause = ValueError('NOT_FOUND')
    e = TokeoDramatiqRefusedError('mq.example:5672', cause)
    assert str(e) == 'Rabbitmq at mq.example:5672 refused the request (ValueError: NOT_FOUND)!'
    assert (e.address, e.cause) == ('mq.example:5672', 'ValueError: NOT_FOUND')


def test_queue_error():
    e = TokeoDramatiqQueueError('mail', 'has no actor, its arguments are unknown')
    assert str(e) == 'Queue mail has no actor, its arguments are unknown!'
    assert (e.queue, e.reason) == ('mail', 'has no actor, its arguments are unknown')


@pytest.mark.parametrize(
    'error',
    [
        TokeoDramatiqConfigError('config', 'max_age', 0, 'null'),
        TokeoDramatiqConnectionError('localhost:5672', OSError()),
        TokeoDramatiqRefusedError('localhost:5672', OSError()),
        TokeoDramatiqQueueError('mail', 'is missing'),
    ],
)
def test_errors_are_tokeo_errors(error):
    # main catches them as tokeo errors, only a lost connection is a dramatiq one
    assert isinstance(error, TokeoDramatiqError) and isinstance(error, TokeoError)
    assert isinstance(error, ConnectionClosed) == isinstance(error, TokeoDramatiqConnectionError)


def test_old_import_path():
    from tokeo.ext.dramatiq import base

    assert base.TokeoDramatiqError is TokeoDramatiqError
    assert base.TokeoDramatiqConnectionError is TokeoDramatiqConnectionError


def _errors():
    # the cause of a real send to a closed port, pika cannot pickle it
    socket_error = AMQPConnectorSocketConnectError(ConnectionRefusedError(111, 'refused'))
    refused = ConnectionClosed(pika.exceptions.AMQPConnectionError(socket_error))
    return [
        TokeoDramatiqConfigError('config', 'max_age', {'a': 1}, 'null'),
        TokeoDramatiqConnectionError('localhost:5672', refused, actor_name='count_words'),
        TokeoDramatiqRefusedError('localhost:5672', pika.exceptions.ChannelClosedByBroker(403, 'ACCESS_REFUSED')),
        TokeoDramatiqQueueError('mail', 'is missing'),
    ]


def _pickled(error):
    return pickle.loads(pickle.dumps(error))


@pytest.mark.parametrize('index', range(4), ids=['config', 'connection', 'refused', 'queue'])
@pytest.mark.parametrize('clone', [copy.copy, copy.deepcopy, _pickled], ids=['copy', 'deepcopy', 'pickle'])
def test_errors_survive_copy_and_pickle(index, clone):
    error = _errors()[index]
    error.add_note('a note')
    cloned = clone(error)
    assert type(cloned) is type(error) and cloned.args == error.args and str(cloned) == str(error)
    assert vars(cloned) == vars(error) and cloned.__notes__ == ['a note']


def test_errors_survive_a_process_pool():
    # a process pool pickles them on the way in and out
    errors = _errors()
    with multiprocessing.get_context('spawn').Pool(1) as pool:
        returned = pool.apply_async(copy.copy, (errors,)).get(timeout=60)
    assert [str(e) for e in returned] == [str(e) for e in errors]
