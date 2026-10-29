"""
Tokeo Dramatiq queue management.

State and maintenance of the queues of the app by AMQP only. Each queue comes
with its delay queue (DQ) and its dead letter queue (XQ), which are handled
together under the name of the queue.

"""

import copy
import time
import importlib
import threading
import dataclasses
import pika
from dramatiq.common import dq_name, xq_name, q_name
from dramatiq.message import Message
from dramatiq.brokers import rabbitmq as dramatiq_rabbitmq
from tokeo.ext.dramatiq.exc import (
    TokeoDramatiqConfigError,
    TokeoDramatiqConnectionError,
    TokeoDramatiqRefusedError,
    TokeoDramatiqQueueError,
)

#: Characters of the arguments shown by peek_dead
ARGS_PREVIEW_LENGTH = 500


class TokeoDramatiqQueues:
    """
    Queue state and maintenance, available as ```app.dramatiq.queues```.

    All methods return plain dicts or lists of dicts. Without names they work
    on all queues of the actors of the app.

    ### Args

    - **dramatiq** (TokeoDramatiq): The dramatiq extension of the app,
        ```app.dramatiq```

    ### Notes

    : One connection to rabbitmq is opened on first use and kept for the
        following calls; a connection closed by rabbitmq in the meantime is
        opened again on the next call

    : A failed connection raises ```TokeoDramatiqConnectionError```, a
        request refused by rabbitmq ```TokeoDramatiqRefusedError```

    """

    def __init__(self, dramatiq):
        self._dramatiq = dramatiq
        self._lock = threading.Lock()
        self._connection = None
        self._channel = None
        self._cache = {}

    # ---- connection --------------------------------------------------------------

    def close(self):
        """Close the connection to rabbitmq, the next call opens a new one."""
        with self._lock:
            self._close()

    def _close(self):
        connection, self._connection, self._channel = self._connection, None, None
        if connection is not None and connection.is_open:
            try:
                connection.close()
            except Exception:
                pass

    def _open(self):
        # reuse an open connection, it serves its heartbeats on each call
        if self._connection is None or self._connection.is_closed:
            self._connection = pika.BlockingConnection(self._dramatiq.broker.parameters)
            self._channel = None
        self._connection.process_data_events(0)
        if self._channel is None or self._channel.is_closed:
            self._channel = self._connection.channel()
            self._channel.confirm_delivery()
        return self._channel

    def _call(self, func, *args):
        with self._lock:
            return self._run(func, *args)

    def _run(self, func, *args):
        # a connection closed by rabbitmq gets opened again once
        for attempt in range(2):
            try:
                self._open()
                return func(*args)
            except pika.exceptions.AMQPConnectionError as e:
                self._close()
                if attempt:
                    raise TokeoDramatiqConnectionError(self._dramatiq.broker.address, e) from e
            except pika.exceptions.AMQPChannelError as e:
                # rabbitmq closes the channel on a refused request
                self._channel = None
                raise TokeoDramatiqRefusedError(self._dramatiq.broker.address, e) from e

    def _declared(self, queue):
        # passive declare answers without creating a missing queue
        try:
            return self._open().queue_declare(queue, passive=True).method
        except pika.exceptions.ChannelClosedByBroker as e:
            if e.reply_code != 404:
                raise
            self._channel = None
            return None

    # ---- names -------------------------------------------------------------------

    def _actors(self):
        broker = self._dramatiq.broker
        # the cli knows no actors until their module is imported
        if not broker.get_declared_actors():
            module = self._dramatiq.config('actors')
            try:
                importlib.import_module(module)
            except ImportError as e:
                raise TokeoDramatiqConfigError('config', 'actors', module, f'an importable module ({e})') from None
        actors = {}
        for name in broker.get_declared_actors():
            actors.setdefault(broker.get_actor(name).queue_name, []).append(name)
        return actors

    def names(self):
        """
        Get the names of the queues of the actors of the app.

        ### Returns

        - **list**: The sorted queue names

        """
        return sorted(self._actors())

    def _names(self, names):
        if names is None:
            return self.names()
        return [q_name(name) for name in ([names] if isinstance(names, str) else names)]

    # ---- state -------------------------------------------------------------------

    def state(self, names=None, cache_ttl=0):
        """
        Get the state of queues without creating missing ones.

        ### Args

        - **names** (str|list, optional): Queue names, all queues of the app
            without
        - **cache_ttl** (float, optional): Seconds a state is kept for further
            calls. Defaults to 0, no cache

        ### Returns

        - **list**: One dict per queue with ```name```, ```exists```,
            ```ready```, ```delayed```, ```dead```, ```consumers```,
            ```unparalleled``` and ```actors```

        ### Notes

        : ```delayed``` misses the messages workers have prefetched already

        """
        actors = self._actors()
        names = self._names(names)
        key = tuple(names)
        # one lock for check and query, waiting callers get the fresh state
        with self._lock:
            cached = self._cache.get(key)
            if cached is None or time.monotonic() - cached[0] >= cache_ttl:
                cached = (time.monotonic(), self._run(self._state, names, actors))
                self._cache[key] = cached
            # a copy, a caller may change its dicts
            return copy.deepcopy(cached[1])

    def _state(self, names, actors):
        result = []
        for name in names:
            queue, delay, dead = (self._declared(n) for n in (name, dq_name(name), xq_name(name)))
            result.append(
                dict(
                    name=name,
                    exists=queue is not None,
                    ready=queue.message_count if queue else None,
                    delayed=delay.message_count if delay else None,
                    dead=dead.message_count if dead else None,
                    consumers=queue.consumer_count if queue else None,
                    unparalleled='_unparalleled' in name.lower(),
                    actors=sorted(actors.get(name, [])),
                )
            )
        return result

    # ---- dead letters ------------------------------------------------------------

    def peek_dead(self, name, limit=10):
        """
        Show dead letters of a queue and leave them in place.

        ### Args

        - **name** (str): The queue name
        - **limit** (int, optional): Dead letters to show. Defaults to 10

        ### Returns

        - **list**: One dict per dead letter with ```message_id```,
            ```actor```, ```retries```, ```error```, ```args``` and
            ```timestamp```

        """
        return self._call(self._peek_dead, q_name(name), limit)

    def _peek_dead(self, name, limit):
        if self._declared(xq_name(name)) is None:
            return []
        channel = self._open()
        result, tag = [], None
        while len(result) < limit:
            method, _, body = channel.basic_get(xq_name(name), auto_ack=False)
            if method is None:
                break
            tag = method.delivery_tag
            result.append(_dead_letter(Message.decode(body)))
        if tag is not None:
            # give all back at once, so the loop never reads one twice
            channel.basic_nack(delivery_tag=tag, multiple=True, requeue=True)
        return result

    def requeue_dead(self, name, limit=None):
        """
        Move dead letters of a queue back into the queue.

        ### Args

        - **name** (str): The queue name
        - **limit** (int, optional): Dead letters to move, all without

        ### Returns

        - **dict**: ```name``` and the number ```requeued```

        ### Raises

        - **TokeoDramatiqQueueError**: If the queue is missing

        ### Notes

        : Retries and traceback get reset; a message is acked in the XQ only
            after rabbitmq confirmed it in the queue

        """
        return self._call(self._requeue_dead, q_name(name), limit)

    def _requeue_dead(self, name, limit):
        self._cache.clear()
        if self._declared(name) is None:
            raise TokeoDramatiqQueueError(name, 'is missing, recreate it before the requeue')
        dead = self._declared(xq_name(name))
        # only the dead letters present now, not ones arriving meanwhile
        count = dead.message_count if dead else 0
        count = count if limit is None else min(count, limit)
        channel = self._open()
        requeued = 0
        for _ in range(count):
            method, _, body = channel.basic_get(xq_name(name), auto_ack=False)
            if method is None:
                break
            message = Message.decode(body)
            options = {k: v for k, v in message.options.items() if k not in ('retries', 'traceback', 'eta')}
            # copy() would merge the options, replace drops the removed ones
            message = dataclasses.replace(message, queue_name=name, options=options)
            try:
                channel.basic_publish(
                    exchange='',
                    routing_key=name,
                    body=message.encode(),
                    properties=pika.BasicProperties(delivery_mode=2, priority=options.get('broker_priority')),
                    mandatory=True,
                )
            except Exception:
                # give the dead letter back at once, not only on close
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                raise
            channel.basic_ack(delivery_tag=method.delivery_tag)
            requeued += 1
        return dict(name=name, requeued=requeued)

    # ---- maintenance -------------------------------------------------------------

    def purge(self, names=None, dead_only=False):
        """
        Remove the messages of queues and keep the queues.

        ### Args

        - **names** (str|list, optional): Queue names, all queues of the app
            without
        - **dead_only** (bool, optional): Purge only the dead letters (XQ)

        ### Returns

        - **list**: One dict per queue with ```name``` and the number
            ```purged```

        """
        return self._call(self._purge, self._names(names), dead_only)

    def _purge(self, names, dead_only):
        self._cache.clear()
        result = []
        for name in names:
            purged = 0
            for queue in (xq_name(name),) if dead_only else (name, dq_name(name), xq_name(name)):
                if self._declared(queue) is not None:
                    purged += self._open().queue_purge(queue).method.message_count
            result.append(dict(name=name, purged=purged))
        return result

    def delete(self, names=None, force=False):
        """
        Delete queues with their delay and dead letter queues.

        ### Args

        - **names** (str|list, optional): Queue names, all queues of the app
            without
        - **force** (bool, optional): Delete also queues with messages or
            consumers

        ### Returns

        - **list**: One dict per queue with ```name``` and ```deleted```

        ### Raises

        - **TokeoDramatiqQueueError**: If a queue has messages or consumers and
            force is not set

        """
        return self._call(self._delete, self._names(names), force)

    def _delete(self, names, force):
        self._cache.clear()
        if not force:
            for state in self._state(names, {}):
                busy = [key for key in ('ready', 'delayed', 'dead', 'consumers') if state[key]]
                if busy:
                    raise TokeoDramatiqQueueError(state['name'], f'has {", ".join(busy)}, use force to delete it anyway')
        result = []
        for name in names:
            deleted = False
            for queue in (name, dq_name(name), xq_name(name)):
                if self._declared(queue) is not None:
                    self._open().queue_delete(queue)
                    deleted = True
            result.append(dict(name=name, deleted=deleted))
        return result

    def recreate(self, names=None, force=False):
        """
        Delete queues and declare them again with the current arguments.

        ### Args

        - **names** (str|list, optional): Queue names, all queues of the app
            without
        - **force** (bool, optional): Recreate also queues with messages or
            consumers

        ### Returns

        - **list**: One dict per queue with ```name``` and ```arguments```

        ### Raises

        - **TokeoDramatiqQueueError**: If a queue has no actor, or has messages or
            consumers and force is not set

        """
        names = self._names(names)
        known = self.names()
        for name in names:
            if name not in known:
                raise TokeoDramatiqQueueError(name, 'has no actor, its arguments are unknown')
        return self._call(self._recreate, names, force)

    def _recreate(self, names, force):
        self._delete(names, force)
        broker = self._dramatiq.broker
        result = []
        for name in names:
            # the same arguments dramatiq declares its queues with
            arguments = broker._build_queue_arguments(name)
            channel = self._open()
            channel.queue_declare(name, durable=True, arguments=arguments)
            channel.queue_declare(dq_name(name), durable=True, arguments=arguments)
            channel.queue_declare(xq_name(name), durable=True, arguments={'x-message-ttl': dramatiq_rabbitmq.DEAD_MESSAGE_TTL})
            result.append(dict(name=name, arguments=arguments))
        return result


def _dead_letter(message):
    """Describe a dead letter by its message."""
    options = message.options
    traceback = (options.get('traceback') or '').strip().splitlines()
    args = repr((message.args, message.kwargs))
    if len(args) > ARGS_PREVIEW_LENGTH:
        args = args[:ARGS_PREVIEW_LENGTH] + '...'
    return dict(
        message_id=message.message_id,
        actor=message.actor_name,
        retries=options.get('retries', 0),
        error=traceback[-1] if traceback else None,
        args=args,
        timestamp=message.message_timestamp,
    )
