"""
Tokeo Dramatiq queue and worker monitoring.

With ```monitoring: true``` each worker process sends a heartbeat every
```monitoring_interval``` seconds to the fanout exchange
```<app label>.dramatiq.monitoring```. A monitor listens on a queue of its own
and shows the workers it heard of together with the state of the queues.

Heartbeats are not persisted, not confirmed and dropped by rabbitmq while no
monitor listens. The arguments of running tasks and the text of errors are
sent with details only (```monitoring_with_details```), as they may hold
personal data or secrets.

"""

import os
import json
import time
import socket
import threading
import importlib.metadata
import pika
from dramatiq.middleware import Middleware
from tokeo.ext.dramatiq.exc import TokeoDramatiqConnectionError

#: Format and version of the heartbeat messages
HEARTBEAT_TYPE = 'tokeo.dramatiq.heartbeat'
HEARTBEAT_VERSION = 1

#: Characters of the arguments and error texts in a heartbeat
DETAILS_LENGTH = 500

#: Intervals without a heartbeat until a worker is late or gone
LATE_INTERVALS = 2
GONE_INTERVALS = 3

#: Intervals a gone or stopped worker is still shown
KEEP_INTERVALS = 10

#: Heartbeats a monitor queue keeps at most while nobody reads it
MONITOR_QUEUE_LENGTH = 1000


def exchange_name(app):
    """The name of the monitoring exchange of the app."""
    return f'{app._meta.label}.dramatiq.monitoring'


def _now_ms():
    return int(time.time() * 1000)


def _cut(text):
    return text if len(text) <= DETAILS_LENGTH else text[:DETAILS_LENGTH] + '...'


def _app_version(app):
    # the version of the distribution named like the package of the app
    try:
        return importlib.metadata.version(type(app).__module__.split('.')[0])
    except Exception:
        return None


class TokeoDramatiqHeartbeat(Middleware):
    """
    Worker middleware that sends a heartbeat of its process to the monitors.

    ### Args

    - **exchange** (str): The monitoring exchange
    - **interval** (int): Seconds between two heartbeats
    - **details** (bool): Whether to send arguments and error texts
    - **app** (dict): Label and version of the app

    ### Notes

    : The heartbeat thread uses a connection of its own, as pika connections
      are not thread-safe; a lost connection is opened again on the next
      heartbeat without disturbing the worker

    """

    def __init__(self, exchange, interval, details, app):
        self.exchange = exchange
        self.interval = interval
        self.details = details
        self.app = app
        self._lock = threading.Lock()
        self._running = {}
        self._totals = dict(done=0, failed=0, dead=0)
        self._since = {}
        self._since_start = time.monotonic()
        self._worker = None
        self._started_at = None
        self._parameters = None
        self._thread = None
        self._stop = threading.Event()
        self._connection = None
        self._channel = None

    # --- worker hooks -------------------------------------------------------------

    def after_worker_boot(self, broker, worker):
        self._worker = worker
        self._started_at = _now_ms()
        # another broker, e.g. the stub broker of tests, has no parameters
        self._parameters = getattr(broker, 'parameters', None)
        self._thread = threading.Thread(target=self._loop, name='tokeo-dramatiq-heartbeat', daemon=True)
        self._thread.start()

    def after_worker_shutdown(self, broker, worker):
        # after the running tasks, so the last heartbeat counts them
        if self._thread is None:
            return
        self._stop.set()
        # a heartbeat being sent ends within the connect timeout of pika
        self._thread.join(timeout=15)
        if self._thread.is_alive():
            return
        # a last heartbeat tells the monitors at once
        self._send(self.heartbeat(state='stopped'))
        self._close()

    def before_process_message(self, broker, message):
        task = dict(
            message_id=message.message_id,
            actor=message.actor_name,
            queue=message.queue_name,
            retries=message.options.get('retries', 0),
            started_at=_now_ms(),
            started=time.monotonic(),
        )
        if self.details:
            task['args'] = _cut(repr((message.args, message.kwargs)))
        with self._lock:
            self._running[message.message_id] = task

    def after_process_message(self, broker, message, *, result=None, exception=None):
        with self._lock:
            task = self._running.pop(message.message_id, None)
            runtime = int((time.monotonic() - task['started']) * 1000) if task else 0
            actor = self._since.setdefault(message.actor_name, dict(done=0, failed=0, max_runtime_ms=0))
            actor['max_runtime_ms'] = max(actor['max_runtime_ms'], runtime)
            if exception is None:
                self._totals['done'] += 1
                actor['done'] += 1
            else:
                self._totals['failed'] += 1
                actor['failed'] += 1
                # the type never holds data, the text only with details
                error = type(exception).__name__
                if self.details and str(exception):
                    error = _cut(f'{error}: {str(exception).splitlines()[0]}')
                actor['last_error'] = error

    def after_skip_message(self, broker, message):
        with self._lock:
            self._running.pop(message.message_id, None)

    def after_nack(self, broker, message):
        # a rejected message goes to the dead letters
        with self._lock:
            self._totals['dead'] += 1

    # --- heartbeat ----------------------------------------------------------------

    def heartbeat(self, state='running'):
        """
        Build the heartbeat of this worker process.

        ### Args

        - **state** (str, optional): ```running``` or ```stopped```

        ### Returns

        - **dict**: The heartbeat, its ```since_last``` part starts anew

        """
        worker = self._worker
        now = time.monotonic()
        with self._lock:
            running = [
                {key: value for key, value in task.items() if key != 'started'} | dict(runtime_ms=int((now - task['started']) * 1000))
                for task in self._running.values()
            ]
            totals = dict(self._totals)
            since_last = dict(ms=int((now - self._since_start) * 1000), actors=self._since)
            self._since, self._since_start = {}, now
        return dict(
            type=HEARTBEAT_TYPE,
            version=HEARTBEAT_VERSION,
            state=state,
            interval=self.interval,
            sent_at=_now_ms(),
            app=self.app,
            worker=dict(
                host=socket.gethostname(),
                pid=os.getpid(),
                started_at=self._started_at,
                threads=getattr(worker, 'worker_threads', None),
                queues=sorted(worker.consumer_whitelist) if getattr(worker, 'consumer_whitelist', None) else None,
                queue_prefetch=getattr(worker, 'queue_prefetch', None),
                delay_queue_prefetch=getattr(worker, 'delay_prefetch', None),
                details=self.details,
            ),
            running=running,
            totals=totals,
            since_last=since_last,
        )

    def _loop(self):
        self._send(self.heartbeat())
        while not self._stop.wait(self.interval):
            self._send(self.heartbeat())

    def _send(self, heartbeat):
        if self._parameters is None:
            return
        try:
            if self._connection is None or self._connection.is_closed:
                self._connection = pika.BlockingConnection(self._parameters)
                self._channel = self._connection.channel()
                self._channel.exchange_declare(self.exchange, exchange_type='fanout')
            # not mandatory: rabbitmq drops it silently while no monitor listens
            self._channel.basic_publish(
                exchange=self.exchange,
                routing_key='',
                body=json.dumps(heartbeat),
                properties=pika.BasicProperties(content_type='application/json', delivery_mode=1),
            )
        except Exception:
            # the next heartbeat tries again
            self._close()

    def _close(self):
        connection, self._connection, self._channel = self._connection, None, None
        if connection is not None and connection.is_open:
            try:
                connection.close()
            except Exception:
                pass


class TokeoDramatiqMonitor:
    """
    Workers and queues of the app, available as ```app.dramatiq.monitor```.

    ### Notes

    : The monitor knows only the workers it heard of since its first call;
      the first call waits one interval to hear each running worker

    : A worker is ```late``` after 2 and ```gone``` after 3 of its intervals
      without a heartbeat; a gone or stopped worker is shown for 10 more
      intervals

    """

    def __init__(self, dramatiq):
        self._dramatiq = dramatiq
        self._lock = threading.Lock()
        self._connection = None
        self._channel = None
        self._workers = {}

    @property
    def interval(self):
        """Seconds between two heartbeats from the config."""
        return self._dramatiq.config('monitoring_interval')

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

    def _listen(self):
        # a queue of this monitor, deleted by rabbitmq when it closes
        self._connection = pika.BlockingConnection(self._dramatiq.broker.parameters)
        self._channel = self._connection.channel()
        exchange = exchange_name(self._dramatiq.app)
        self._channel.exchange_declare(exchange, exchange_type='fanout')
        arguments = {'x-message-ttl': GONE_INTERVALS * self.interval * 1000, 'x-max-length': MONITOR_QUEUE_LENGTH}
        queue = self._channel.queue_declare('', exclusive=True, auto_delete=True, arguments=arguments).method.queue
        self._channel.queue_bind(queue, exchange)
        self._channel.basic_consume(queue, self._on_message, auto_ack=True)

    def _on_message(self, channel, method, properties, body):
        self.receive(body)

    def receive(self, body, received=None):
        """
        Take a heartbeat into the known workers.

        ### Args

        - **body** (bytes|str): The heartbeat as json
        - **received** (float, optional): Receive time of ```time.monotonic```

        """
        try:
            heartbeat = json.loads(body)
            worker = heartbeat['worker']
            key = (worker['host'], worker['pid'], worker['started_at'])
        except (ValueError, TypeError, KeyError):
            # not a heartbeat of this format
            return
        if heartbeat.get('type') == HEARTBEAT_TYPE:
            self._workers[key] = dict(heartbeat=heartbeat, received=time.monotonic() if received is None else received)

    def _collect(self, wait):
        try:
            if self._connection is None or self._connection.is_closed:
                self._listen()
                # each running worker sends within one interval
                wait = max(wait, self.interval)
            deadline = time.monotonic() + wait
            while True:
                self._connection.process_data_events(time_limit=max(0, deadline - time.monotonic()))
                if time.monotonic() >= deadline:
                    break
        except pika.exceptions.AMQPError as e:
            self._close()
            raise TokeoDramatiqConnectionError(self._dramatiq.broker.address, e) from e

    def workers(self, wait=0):
        """
        Get the workers heard of with their state.

        ### Args

        - **wait** (float, optional): Seconds to listen first; the first call
            listens at least one interval

        ### Returns

        - **list**: One dict per worker process with ```state``` (```ok```,
            ```late```, ```gone``` or ```stopped```), ```seen_ms``` since its
            last heartbeat and the ```heartbeat``` itself

        """
        with self._lock:
            self._collect(wait)
            now = time.monotonic()
            result = []
            for key, entry in list(self._workers.items()):
                heartbeat = entry['heartbeat']
                interval = heartbeat.get('interval') or self.interval
                seen = now - entry['received']
                if heartbeat.get('state') == 'stopped':
                    state, ends = 'stopped', KEEP_INTERVALS
                elif seen <= LATE_INTERVALS * interval:
                    state, ends = 'ok', None
                elif seen <= GONE_INTERVALS * interval:
                    state, ends = 'late', None
                else:
                    state, ends = 'gone', GONE_INTERVALS + KEEP_INTERVALS
                if ends is not None and seen > ends * interval:
                    del self._workers[key]
                    continue
                result.append(dict(state=state, seen_ms=int(seen * 1000), heartbeat=heartbeat))
        return sorted(result, key=lambda w: (w['heartbeat']['worker']['host'], w['heartbeat']['worker']['pid']))

    def snapshot(self, wait=0):
        """
        Get workers, actors and queues in one state.

        ### Args

        - **wait** (float, optional): Seconds to listen first, see ```workers```

        ### Returns

        - **dict**: ```at``` in ms, ```monitoring``` and ```interval``` of the
            config, the ```workers```, the ```actors``` summed over the last
            heartbeat of each running worker, the ```queues``` and their
            ```queue_totals```

        """
        workers = self.workers(wait)
        actors = {}
        for worker in workers:
            if worker['state'] not in ('ok', 'late'):
                continue
            for name, counts in worker['heartbeat']['since_last']['actors'].items():
                actor = actors.setdefault(name, dict(done=0, failed=0, max_runtime_ms=0))
                actor['done'] += counts.get('done', 0)
                actor['failed'] += counts.get('failed', 0)
                actor['max_runtime_ms'] = max(actor['max_runtime_ms'], counts.get('max_runtime_ms', 0))
                if counts.get('last_error'):
                    actor['last_error'] = counts['last_error']
        queues = self._dramatiq.queues.state()
        totals = {key: sum(q[key] or 0 for q in queues) for key in ('ready', 'delayed', 'dead', 'consumers')}
        return dict(
            at=_now_ms(),
            monitoring=self._dramatiq.config('monitoring'),
            interval=self.interval,
            workers=workers,
            actors=dict(sorted(actors.items())),
            queues=queues,
            queue_totals=totals,
        )
