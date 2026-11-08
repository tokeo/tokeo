"""
Follows the changes of records by the realtime api of PocketBase, a stream
of server sent events. ```Realtime``` runs in threads of its own and calls
plain functions, ```AsyncRealtime``` runs as task of an asyncio event loop,
e.g. the one of nicegui, and calls functions or coroutines.

### Example

```python
from tokeo.core.pocketbase import TokeoPocketBaseClient
from tokeo.core.pocketbase.realtime import Realtime

def on_order(event):
    print(event.action, event.record.invoice)

pb = TokeoPocketBaseClient('http://127.0.0.1:8090')
pb.auth_with_password('users', 'anna@example.com', 'anna-secret-123')
with Realtime(pb) as realtime:
    realtime.subscribe('orders/*', on_order)
    ...
```

### Notes

: A topic is ```<collection>/*``` for all records or ```<collection>/<id>```
    for one record; ```options``` adds query parameters, e.g. ```expand```,
    ```fields``` or ```filter```, to the records of the events

: PocketBase sends only the events the login of the client may see by the
    list and view rules of the collection

: A reader takes each event off the connection at once and queues it, a
    dispatcher hands it to the callbacks; so slow callbacks never make
    PocketBase hold events. Once ```max_pending``` events wait, the channel
    hangs up, PocketBase frees all it holds for the connection, and the
    channel connects again after the callbacks caught up

: A lost connection is opened again after a growing delay, up to
    ```max_delay_seconds```, and the subscriptions are sent again; events in
    between are lost, reload what you show in ```on_connect```. A connection
    that served at least ```MIN_STABLE_SECONDS``` and was ended by the server
    is opened again at once, so the gap is only the time to connect

: PocketBase ends a connection without events after 5 minutes, any one
    after 30 minutes, the channel then connects again; a connection silent
    for longer than ```read_timeout``` counts as lost, e.g. one whose server
    went away without closing it

: PocketBase takes a refused token as a guest, without an error. Before it
    sends its subscriptions and every ```auth_check_seconds``` the channel
    makes sure the login holds, by ```confirm_login()```; a login that
    cannot be confirmed hangs up the channel with an error instead of a
    silent guest

: The login of a channel cannot change to another identity, PocketBase
    refuses the subscriptions of another login on the same connection; stop
    and start the channel after such a change

"""

import json
import time
import queue
import socket
import asyncio
import logging
import inspect
import threading
from urllib.parse import quote
import httpx
from .exc import TokeoPocketBaseError
from .record import TokeoPocketBaseRecord

#: The logger of this module
LOG = logging.getLogger(__name__)

#: The path of the realtime api
REALTIME_PATH = '/api/realtime'

#: Seconds without data until a connection counts as lost, above the 5
#: minutes after which PocketBase ends a connection without events
READ_TIMEOUT = 330.0

#: The most subscriptions PocketBase takes on one connection
MAX_SUBSCRIPTIONS = 1000

#: Seconds a connection must have served to be opened again at once after
#: the server ended it, e.g. after its idle or its longest time; a shorter
#: one waits the growing delay, so a server ending each connection at once
#: is not asked without pause
MIN_STABLE_SECONDS = 10.0

#: The states of a channel: live while connected with its subscriptions
#: sent, connecting while it tries, stopped before start and after stop
STATUSES = ('live', 'connecting', 'stopped')

# the mark in the queue of events where a (re)connect calls on_connect
_CONNECTED = object()


def format_topic_key(topic, options=None):
    """
    Build the name of a subscription, the topic with its options.

    ### Args

    - **topic** (str): ```<collection>/*``` or ```<collection>/<id>```
    - **options** (dict): Options of the topic, e.g.
        ```{'query': {'expand': 'owner'}}```

    ### Returns

    - **str**: The name PocketBase uses for the subscription and its events

    """
    if not options:
        return topic
    return f'{topic}?options={quote(json.dumps(options))}'


def confirm_login(client):
    """
    Make sure the login of a client holds on the server.

    ### Args

    - **client** (TokeoPocketBaseClient): The client of a channel

    ### Raises

    - **TokeoPocketBaseAuthError**: If the server refuses the login
    - **TokeoPocketBaseError**: If the check fails otherwise

    ### Notes

    : The ```before_request``` of the client keeps its login, e.g. an
        ```AuthKeeper```; without one a token is confirmed by
        ```auth_refresh()```, and a client without token is a guest by intent

    """
    if client.before_request:
        client.before_request()
    elif client.token:
        client.auth_refresh()


class RealtimeEvent:
    """
    A change of a record sent by PocketBase.

    ### Args

    - **topic** (str): The subscription the event belongs to
    - **data** (dict): The data of the event

    """

    def __init__(self, topic, data):
        #: The subscription the event belongs to, with its options
        self.topic = topic
        #: What happened: create, update or delete
        self.action = data.get('action')
        #: The record after the change, before it for a delete
        self.record = TokeoPocketBaseRecord(data.get('record'))

    def __repr__(self):
        return f'RealtimeEvent({self.topic!r}, {self.action!r}, {self.record.get("id")!r})'


class _SseParser:
    """
    Read the lines of a stream of server sent events.

    ### Notes

    : ```feed()``` takes one line after the other and gives an event once
        its blank line closes it, else None

    """

    def __init__(self):
        self._event = None
        self._data = []

    def feed(self, line):
        """
        Take one line of the stream.

        ### Args

        - **line** (str): The line without its line break

        ### Returns

        - **tuple|None**: ```(event, data)``` of a complete event, the data
            parsed from json, else None

        """
        if line == '':
            # a blank line closes the event
            if not self._data:
                self._event = None
                return None
            event, data = self._event or 'message', '\n'.join(self._data)
            self._event, self._data = None, []
            try:
                return event, json.loads(data)
            except ValueError:
                return event, data
        field, _, value = line.partition(':')
        value = value[1:] if value.startswith(' ') else value
        if field == 'event':
            self._event = value
        elif field == 'data':
            self._data.append(value)
        return None


class _Subscriptions:
    """The callbacks of each subscription of a channel, shared by both channels."""

    def __init__(self):
        #: name of the subscription: list of callbacks
        self.callbacks = {}
        #: Called after each (re)connect with the subscriptions sent
        self.on_connect = None
        #: The id PocketBase gave the current connection
        self.client_id = None
        #: The login count of the client when the subscriptions were sent last
        self.login_cnt = None

    def add(self, topic, callback, options):
        # add a callback, True when the subscription is new
        key = format_topic_key(topic, options)
        new = key not in self.callbacks
        self.callbacks.setdefault(key, []).append(callback)
        return new

    def remove(self, topic, callback, options):
        # remove one callback or all of a subscription, True when it is gone
        key = format_topic_key(topic, options)
        if key not in self.callbacks:
            return False
        if callback is not None and callback in self.callbacks[key]:
            self.callbacks[key].remove(callback)
        if callback is None or not self.callbacks[key]:
            del self.callbacks[key]
            return True
        return False

    def body(self):
        # the body that sets all subscriptions of the connection
        return {'clientId': self.client_id, 'subscriptions': list(self.callbacks)}


class Realtime:
    """
    A realtime channel in threads of its own, calling plain functions.

    ### Args

    - **client** (TokeoPocketBaseClient): The client whose login the channel uses

    ### Keyword Args

    - **max_delay_seconds** (float): The longest delay in seconds before a new try
        to connect, also the pause after the callbacks fell behind
    - **read_timeout** (float): Seconds without data until a connection
        counts as lost
    - **max_pending** (int): The events that may wait for the callbacks
        before the channel hangs up, at least 1
    - **auth_check_seconds** (float): Seconds between two checks that the login
        still holds

    ### Notes

    : A reader thread takes the events off the connection, a dispatcher
        thread calls the callbacks one after the other; a callback that
        raises is logged and the channel goes on

    : Use it as context manager or call ```start()``` and ```stop()```

    : ```stop()``` ends the connection at once; a callback still running
        ends on its own, its thread then calls no other callback and ends
        too, even when the channel was started again meanwhile

    """

    def __init__(self, client, max_delay_seconds=30.0, read_timeout=READ_TIMEOUT, max_pending=1000, auth_check_seconds=60.0):
        if max_pending < 1:
            raise ValueError('max_pending must be at least 1')
        #: The client whose login the channel uses
        self.client = client
        #: The longest delay in seconds before a new try to connect
        self.max_delay_seconds = max_delay_seconds
        #: Seconds without data until a connection counts as lost
        self.read_timeout = read_timeout
        #: The events that may wait for the callbacks before the channel hangs up
        self.max_pending = max_pending
        #: Seconds between two checks that the login still holds
        self.auth_check_seconds = auth_check_seconds
        #: Set while a connection is open and its subscriptions are sent
        self.connected_event = threading.Event()
        #: The last failure of the channel, None since it connected
        self.error = None
        self._subscriptions = _Subscriptions()
        self._lock = threading.Lock()
        # each start gets its own stop event, http client and queue; threads
        # still running after stop() keep the ones they were started with
        self._stop = threading.Event()
        self._pending = None
        self._thread = None
        self._dispatcher = None
        self._http = None
        self._response = None
        # the delay before the next try to connect, doubled after each failure
        self._delay = 0.5

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def on_connect(self):
        """Called without arguments after each connect and reconnect."""
        return self._subscriptions.on_connect

    @on_connect.setter
    def on_connect(self, callback):
        self._subscriptions.on_connect = callback

    @property
    def status(self):
        """The state of the channel, one of ```STATUSES```."""
        if self.connected_event.is_set():
            return 'live'
        return 'stopped' if self._thread is None else 'connecting'

    @property
    def topics(self):
        """The names of the subscriptions of the channel, with their options."""
        return list(self._subscriptions.callbacks)

    def subscribe(self, topic, callback, options=None):
        """
        Call a function for each event of a topic.

        ### Args

        - **topic** (str): ```<collection>/*``` or ```<collection>/<id>```
        - **callback** (callable): Called with the ```RealtimeEvent```
        - **options** (dict): Options of the topic, e.g.
            ```{'query': {'expand': 'owner'}}```

        ### Returns

        - **Realtime**: The channel

        """
        with self._lock:
            if self._subscriptions.add(topic, callback, options) and self.connected_event.is_set():
                self._send_subscriptions()
        return self

    def unsubscribe(self, topic, callback=None, options=None):
        """
        Stop calling a function, or all functions, of a topic.

        ### Args

        - **topic** (str): The topic as subscribed
        - **callback** (callable): The function, None for all of the topic
        - **options** (dict): The options as subscribed

        ### Returns

        - **Realtime**: The channel

        """
        with self._lock:
            if self._subscriptions.remove(topic, callback, options) and self.connected_event.is_set():
                self._send_subscriptions()
        return self

    def start(self):
        """
        Open the channel in its threads.

        ### Returns

        - **Realtime**: The channel

        """
        if self._thread is None:
            self._stop = threading.Event()
            self._response = None
            self._pending = queue.Queue(maxsize=self.max_pending)
            timeout = httpx.Timeout(10.0, read=self.read_timeout)
            self._http = httpx.Client(base_url=self.client.url, **{**self.client._httpx_kwargs, 'timeout': timeout})
            run = (self._stop, self._http, self._pending)
            self._thread = threading.Thread(target=self._run, args=run, name='tokeo-pocketbase-realtime', daemon=True)
            self._dispatcher = threading.Thread(
                target=self._dispatch, args=(self._stop, self._pending), name='tokeo-pocketbase-realtime-dispatch', daemon=True
            )
            self._thread.start()
            self._dispatcher.start()
        return self

    def stop(self, timeout=5.0):
        """
        Close the channel and wait for its threads.

        ### Args

        - **timeout** (float): The seconds to wait for each thread

        ### Notes

        : Called by a callback it does not wait, its thread ends after the
            callback

        """
        if self._thread is None:
            return
        with self._lock:
            self._stop.set()
        self._close_connection()
        self._http.close()
        # wake the dispatcher waiting for an event
        try:
            self._pending.put_nowait(None)
        except queue.Full:
            pass
        for thread in (self._thread, self._dispatcher):
            if thread is not threading.current_thread():
                thread.join(timeout)
        self._thread = self._dispatcher = None
        self._response = None
        self.connected_event.clear()

    def _close_connection(self):
        # end the current connection at once; a read blocked on its socket
        # wakes up only by the shutdown of the socket
        with self._lock:
            response = self._response
        stream = response.extensions.get('network_stream') if response is not None else None
        sock = stream.get_extra_info('socket') if stream is not None else None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _send_subscriptions(self):
        # with the lock held: confirm the login, then set all subscriptions
        confirm_login(self.client)
        self.client._send('POST', REALTIME_PATH, json=self._subscriptions.body())
        self._subscriptions.login_cnt = self.client.login_cnt

    def _run(self, stop, http, pending):
        # reader: connect, read and connect again until stopped
        while not stop.is_set():
            # a connection starts once the callbacks caught up
            while not pending.empty():
                if stop.wait(0.1):
                    return
            overflow, ended, start_time = False, False, time.monotonic()
            try:
                overflow = self._listen(stop, http, pending)
                ended = not overflow
            except Exception as err:
                # closing the client by stop() ends the stream with an error too
                if not stop.is_set():
                    self.error = f'connection lost, {err}'
                    LOG.warning('pocketbase realtime: %s', self.error)
            if stop.is_set():
                return
            served = ended and self.connected_event.is_set() and time.monotonic() - start_time >= MIN_STABLE_SECONDS
            self.connected_event.clear()
            # the server ended a connection that served: connect at once
            if served:
                continue
            if stop.wait(self.max_delay_seconds if overflow else self._delay):
                return
            self._delay = min(self._delay * 2, self.max_delay_seconds)

    def _listen(self, stop, http, pending):
        # one connection: subscribe on its connect event, then queue the
        # events without waiting for the callbacks; True when they fell behind
        parser = _SseParser()
        with http.stream('GET', REALTIME_PATH) as response:
            with self._lock:
                # stop() came first, its shutdown missed this connection
                if stop.is_set():
                    return False
                self._response = response
            response.raise_for_status()
            for line in response.iter_lines():
                # a thread past its stop() takes nothing more
                if stop.is_set():
                    return False
                parsed = parser.feed(line)
                if parsed is None:
                    continue
                name, data = parsed
                if name == 'PB_CONNECT':
                    with self._lock:
                        if stop.is_set():
                            return False
                        self._subscriptions.client_id = data['clientId']
                        self._send_subscriptions()
                        self.connected_event.set()
                    self.error = None
                    # a connection that came up starts the delays anew
                    self._delay = 0.5
                    # the queue is empty at connect, the mark always fits
                    pending.put_nowait(_CONNECTED)
                    continue
                try:
                    pending.put_nowait(RealtimeEvent(name, data))
                except queue.Full:
                    self.error = f'{self.max_pending} events wait for the callbacks, connection closed'
                    LOG.warning('pocketbase realtime: %s', self.error)
                    return True
        return False

    def _dispatch(self, stop, pending):
        # dispatcher: hand each event to its callbacks, check the login
        last_check_time = time.monotonic()
        while not stop.is_set():
            if self.connected_event.is_set() and time.monotonic() - last_check_time >= self.auth_check_seconds:
                last_check_time = time.monotonic()
                try:
                    with self._lock:
                        if stop.is_set():
                            return
                        confirm_login(self.client)
                        # a new login: PocketBase dropped the old one
                        if self.client.login_cnt != self._subscriptions.login_cnt:
                            self._send_subscriptions()
                except Exception as err:
                    self.error = f'login not confirmed, {err}'
                    LOG.error('pocketbase realtime: %s', self.error)
                    self.connected_event.clear()
                    self._close_connection()
            try:
                item = pending.get(timeout=min(1.0, self.auth_check_seconds))
            except queue.Empty:
                continue
            # a thread past its stop() hands on nothing more
            if stop.is_set():
                return
            if item is _CONNECTED:
                last_check_time = time.monotonic()
                if self.on_connect:
                    self._call(self.on_connect)
            elif item is not None:
                for callback in list(self._subscriptions.callbacks.get(item.topic, [])):
                    self._call(callback, item)

    def _call(self, callback, *args):
        # a failing callback does not stop the channel
        try:
            callback(*args)
        except Exception:
            LOG.exception('pocketbase realtime: callback %r failed', callback)


class AsyncRealtime:
    """
    A realtime channel as task of an asyncio event loop, e.g. of nicegui.

    ### Args

    - **client** (TokeoPocketBaseClient): The client whose login the channel uses

    ### Keyword Args

    - **max_delay_seconds** (float): The longest delay in seconds before a new try
        to connect, also the pause after the callbacks fell behind
    - **read_timeout** (float): Seconds without data until a connection
        counts as lost
    - **max_pending** (int): The events that may wait for the callbacks
        before the channel hangs up, at least 1
    - **auth_check_seconds** (float): Seconds between two checks that the login
        still holds

    ### Example

    ```python
    realtime = AsyncRealtime(app.db.pb)
    realtime.subscribe('orders/*', on_order)
    task = asyncio.create_task(realtime.run())
    ...
    await realtime.stop()
    ```

    ### Notes

    : A reader task takes the events off the connection, a dispatcher task
        calls the callbacks one after the other and awaits a coroutine; a
        callback that raises is logged and the channel goes on

    : A callback must not block the event loop, e.g. by a request of the
        synchronous client; run such work by ```asyncio.to_thread```

    : The ```before_request``` of the client, e.g. the login of a service
        identity, runs in a thread before the subscriptions are sent

    : One ```run()``` at a time, a second one raises

    """

    def __init__(self, client, max_delay_seconds=30.0, read_timeout=READ_TIMEOUT, max_pending=1000, auth_check_seconds=60.0):
        if max_pending < 1:
            raise ValueError('max_pending must be at least 1')
        #: The client whose login the channel uses
        self.client = client
        #: The longest delay in seconds before a new try to connect
        self.max_delay_seconds = max_delay_seconds
        #: Seconds without data until a connection counts as lost
        self.read_timeout = read_timeout
        #: The events that may wait for the callbacks before the channel hangs up
        self.max_pending = max_pending
        #: Seconds between two checks that the login still holds
        self.auth_check_seconds = auth_check_seconds
        #: Set while a connection is open and its subscriptions are sent
        self.connected_event = asyncio.Event()
        #: The last failure of the channel, None since it connected
        self.error = None
        #: Called after each connect and reconnect, a function or coroutine
        self.on_connect = None
        self._subscriptions = _Subscriptions()
        self._stopping = False
        self._http = None
        self._task = None
        self._listen_task = None
        # one sending of the subscriptions at a time, so the last one wins
        self._send_lock = asyncio.Lock()
        # the tasks sending changed subscriptions, kept until they are done
        self._sends = set()
        # the delay before the next try to connect, doubled after each failure
        self._delay = 0.5

    @property
    def status(self):
        """The state of the channel, one of ```STATUSES```."""
        if self.connected_event.is_set():
            return 'live'
        return 'connecting' if self._task is not None and not self._task.done() else 'stopped'

    @property
    def topics(self):
        """The names of the subscriptions of the channel, with their options."""
        return list(self._subscriptions.callbacks)

    def subscribe(self, topic, callback, options=None):
        """
        Call a function or coroutine for each event of a topic.

        ### Args

        - **topic** (str): ```<collection>/*``` or ```<collection>/<id>```
        - **callback** (callable): Called with the ```RealtimeEvent```
        - **options** (dict): Options of the topic

        ### Returns

        - **AsyncRealtime**: The channel

        ### Notes

        : While connected the new subscriptions are sent by a task of the
            running loop, ```flush()``` waits for it

        """
        if self._subscriptions.add(topic, callback, options) and self.connected_event.is_set():
            self._resend_subscriptions()
        return self

    def unsubscribe(self, topic, callback=None, options=None):
        """
        Stop calling a function, or all functions, of a topic.

        ### Args

        - **topic** (str): The topic as subscribed
        - **callback** (callable): The function, None for all of the topic
        - **options** (dict): The options as subscribed

        ### Returns

        - **AsyncRealtime**: The channel

        """
        if self._subscriptions.remove(topic, callback, options) and self.connected_event.is_set():
            self._resend_subscriptions()
        return self

    async def flush(self):
        """Wait until the changed subscriptions are sent to PocketBase."""
        await asyncio.gather(*self._sends, return_exceptions=True)

    async def run(self):
        """Connect, read and connect again until ```stop()```."""
        if self._task is not None and not self._task.done():
            raise TokeoPocketBaseError('pocketbase realtime: the channel runs already')
        self._stopping = False
        self._task = asyncio.current_task()
        timeout = httpx.Timeout(10.0, read=self.read_timeout)
        self._http = httpx.AsyncClient(base_url=self.client.url, **{**self.client._httpx_kwargs, 'timeout': timeout})
        pending = asyncio.Queue(maxsize=self.max_pending)
        dispatcher = asyncio.create_task(self._dispatch(pending))
        listen = None
        try:
            while not self._stopping:
                # a connection starts once the callbacks caught up
                while not pending.empty():
                    await asyncio.sleep(0.1)
                start_time = time.monotonic()
                listen = self._listen_task = asyncio.create_task(self._listen(pending))
                await asyncio.wait({listen})
                overflow, ended = False, False
                if listen.cancelled():
                    # hung up after the login was not confirmed, already logged
                    pass
                elif listen.exception() is not None:
                    self.error = f'connection lost, {listen.exception()}'
                    LOG.warning('pocketbase realtime: %s', self.error)
                else:
                    overflow = listen.result()
                    ended = not overflow
                served = ended and self.connected_event.is_set() and time.monotonic() - start_time >= MIN_STABLE_SECONDS
                self.connected_event.clear()
                # the server ended a connection that served: connect at once
                if served:
                    continue
                await asyncio.sleep(self.max_delay_seconds if overflow else self._delay)
                self._delay = min(self._delay * 2, self.max_delay_seconds)
        except asyncio.CancelledError:
            if not self._stopping:
                raise
        finally:
            self.connected_event.clear()
            tasks = [task for task in (listen, dispatcher) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._http.aclose()

    async def stop(self):
        """Close the channel and wait until ```run()``` ended."""
        self._stopping = True
        tasks = [task for task in (self._task, *self._sends) if task is not None and not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None

    async def _send_subscriptions(self):
        # confirm the login, then set all subscriptions; the lock keeps the
        # order, so the last change wins
        async with self._send_lock:
            await asyncio.to_thread(confirm_login, self.client)
            headers = {'Authorization': self.client.token} if self.client.token else {}
            response = await self._http.post(REALTIME_PATH, json=self._subscriptions.body(), headers=headers)
            if response.status_code >= 400:
                raise TokeoPocketBaseError(
                    f'POST {self.client.url}{REALTIME_PATH} failed with {response.status_code}: {response.text[:200]}',
                    status=response.status_code,
                )
            self._subscriptions.login_cnt = self.client.login_cnt

    def _resend_subscriptions(self):
        # a change of the subscriptions while connected, sent by a task of the
        # running loop, a failure is logged
        async def send():
            try:
                await self._send_subscriptions()
            except (httpx.HTTPError, TokeoPocketBaseError) as err:
                LOG.warning('pocketbase realtime: subscriptions not sent, %s', err)

        task = asyncio.get_running_loop().create_task(send())
        self._sends.add(task)
        task.add_done_callback(self._sends.discard)

    async def _listen(self, pending):
        # one connection: subscribe on its connect event, then queue the
        # events without waiting for the callbacks; True when they fell behind
        parser = _SseParser()
        async with self._http.stream('GET', REALTIME_PATH) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                parsed = parser.feed(line)
                if parsed is None:
                    continue
                name, data = parsed
                if name == 'PB_CONNECT':
                    self._subscriptions.client_id = data['clientId']
                    await self._send_subscriptions()
                    self.connected_event.set()
                    self.error = None
                    # a connection that came up starts the delays anew
                    self._delay = 0.5
                    # the queue is empty at connect, the mark always fits
                    pending.put_nowait(_CONNECTED)
                    continue
                try:
                    pending.put_nowait(RealtimeEvent(name, data))
                except asyncio.QueueFull:
                    self.error = f'{self.max_pending} events wait for the callbacks, connection closed'
                    LOG.warning('pocketbase realtime: %s', self.error)
                    return True
        return False

    async def _dispatch(self, pending):
        # dispatcher: hand each event to its callbacks, check the login
        last_check_time = time.monotonic()
        while True:
            if self.connected_event.is_set() and time.monotonic() - last_check_time >= self.auth_check_seconds:
                last_check_time = time.monotonic()
                try:
                    await asyncio.to_thread(confirm_login, self.client)
                    # a new login: PocketBase dropped the old one
                    if self.client.login_cnt != self._subscriptions.login_cnt:
                        await self._send_subscriptions()
                except Exception as err:
                    self.error = f'login not confirmed, {err}'
                    LOG.error('pocketbase realtime: %s', self.error)
                    self.connected_event.clear()
                    if self._listen_task is not None:
                        self._listen_task.cancel()
            try:
                item = await asyncio.wait_for(pending.get(), timeout=min(1.0, self.auth_check_seconds))
            except TimeoutError:
                continue
            if item is _CONNECTED:
                last_check_time = time.monotonic()
                if self.on_connect:
                    await self._call(self.on_connect)
            else:
                for callback in list(self._subscriptions.callbacks.get(item.topic, [])):
                    await self._call(callback, item)

    async def _call(self, callback, *args):
        # a failing callback does not stop the channel, a coroutine is awaited
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result
        except Exception:
            LOG.exception('pocketbase realtime: callback %r failed', callback)
