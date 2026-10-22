"""
Tokeo Dramatiq extension errors.

All errors of the extension derive from ```TokeoDramatiqError```. The
subclasses keep the details of the error as fields and build their message
from them. The fields hold plain values only, so the errors survive copy and
pickle, e.g. on their way out of a process pool.

"""

from dramatiq.errors import ConnectionClosed
from tokeo.core.exc import TokeoError

#: How a source of a value is named in the messages
SOURCE_LABELS = {'config': 'dramatiq config', 'env': 'env', 'switch': 'switch'}


def _describe(cause):
    # pika errors cannot be pickled, so a cause is kept as text
    return cause if isinstance(cause, str) else f'{type(cause).__name__}: {cause}'


class TokeoDramatiqError(TokeoError):
    """Errors of the dramatiq extension."""


class TokeoDramatiqConfigError(TokeoDramatiqError):
    """
    A value of the config, an env or a switch is not allowed.

    ### Args

    - **source** (str): Where the value comes from, ```config```, ```env```
        or ```switch```
    - **name** (str): The key, env or switch, e.g. ```rabbitmq.max_priority```
    - **value** (any): The value that is not allowed
    - **expected** (str): What is allowed, e.g. ```true or false```

    """

    def __init__(self, source, name, value, expected):
        self.source = source
        self.name = name
        self.value = value
        self.expected = expected
        super().__init__(f'Invalid {SOURCE_LABELS.get(source, source)} {name}: Value {value!r} must be {expected}!')

    def __reduce__(self):
        return (type(self), (self.source, self.name, self.value, self.expected), self.__dict__)


class TokeoDramatiqConnectionError(TokeoDramatiqError, ConnectionClosed):
    """
    Rabbitmq is not reachable, also a ```ConnectionClosed``` of dramatiq.

    ### Args

    - **address** (str|None): Host and port of rabbitmq, never the url
    - **cause** (Exception|str): The error of pika or dramatiq, kept as text;
        the error itself stays in ```__cause__```
    - **actor_name** (str, optional): The actor, when sending failed

    """

    def __init__(self, address, cause, actor_name=None):
        self.address = address
        self.cause = _describe(cause)
        self.actor_name = actor_name
        action = f'send {actor_name} to' if actor_name else 'connect to'
        where = f'rabbitmq at {address}' if address else 'the broker'
        super().__init__(f'Cannot {action} {where} ({self.cause})!')
        # the dramatiq error base keeps the init arguments as args
        self.args = (self.message,)

    def __reduce__(self):
        return (type(self), (self.address, self.cause, self.actor_name), self.__dict__)


class TokeoDramatiqRefusedError(TokeoDramatiqError):
    """
    Rabbitmq refused a request, the connection works.

    ### Args

    - **address** (str): Host and port of rabbitmq, never the url
    - **cause** (Exception|str): The error of pika, kept as text; the error
        itself stays in ```__cause__```

    """

    def __init__(self, address, cause):
        self.address = address
        self.cause = _describe(cause)
        super().__init__(f'Rabbitmq at {address} refused the request ({self.cause})!')

    def __reduce__(self):
        return (type(self), (self.address, self.cause), self.__dict__)


class TokeoDramatiqQueueError(TokeoDramatiqError):
    """
    The state of a queue prevents the action.

    ### Args

    - **queue** (str): The queue name
    - **reason** (str): Why, e.g. ```is missing, recreate it before the requeue```

    """

    def __init__(self, queue, reason):
        self.queue = queue
        self.reason = reason
        super().__init__(f'Queue {queue} {reason}!')

    def __reduce__(self):
        return (type(self), (self.queue, self.reason), self.__dict__)
