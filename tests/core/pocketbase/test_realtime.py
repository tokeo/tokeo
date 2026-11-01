"""
Tests for tokeo.core.pocketbase.realtime without a server: the names of
subscriptions, the parser of server sent events, the events, and both
channels, in threads and for asyncio, with their subscriptions on
connect, the events handed to the callbacks, changes while connected,
failing callbacks, reconnects with a growing delay, silent connections, a
stop that leaves no thread, connection or task behind, callbacks that fall
behind and the login confirmed before and while the channel runs.

A channel talks to an httpx.MockTransport that answers like the realtime
api of PocketBase: each GET takes the next scripted connection, the last
one stays open until the test ends, each POST of the subscriptions is
recorded, each refresh of the login answers with the same token unless
scripted otherwise. Only the tests of a silent connection talk to a small
http server on a real socket that connects and then sends nothing more.
"""

import json
import time
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from tokeo.core.pocketbase import PocketBase, TokeoPocketBaseAuthError, TokeoPocketBaseError
from tokeo.core.pocketbase.realtime import STATUSES, AsyncRealtime, Realtime, RealtimeEvent, SseParser, confirm_login, topic_key
from tests.utils import wait_until, wait_until_async

URL = 'http://pocketbase.test:8090'

EXPAND = {'query': {'expand': 'owner'}}


class _Server:
    """Answers like the realtime api of PocketBase and records each request."""

    def __init__(self, asynchronous=False):
        # every request in the order it came in
        self.requests = []
        # each GET takes the next: a status or the events of a connection
        self.connections = []
        # each POST of the subscriptions takes the next status, else 204
        self.post_status = []
        # each refresh of the login takes the next status, 200 when none is left
        self.refresh_status = []
        # ends the last open connection of a channel in a thread
        self.release = threading.Event()
        self.asynchronous = asynchronous

    def handle(self, request):
        self.requests.append(request)
        if request.url.path.endswith('/auth-refresh'):
            status = self.refresh_status.pop(0) if self.refresh_status else 200
            if status >= 400:
                return httpx.Response(status, json={'message': 'refused'})
            return httpx.Response(200, json={'token': request.headers['Authorization'], 'record': {'id': 'u1'}})
        if request.method == 'POST':
            status = self.post_status.pop(0) if self.post_status else 204
            return httpx.Response(status, json={'message': 'refused'} if status >= 400 else None)
        if not self.connections:
            return httpx.Response(503)
        connection = self.connections.pop(0)
        if isinstance(connection, int):
            return httpx.Response(connection)
        stream = self._async_stream if self.asynchronous else self._stream
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=stream(connection, not self.connections))

    def _stream(self, events, last):
        # an event in the script holds the stream until it is set
        for event in events:
            if isinstance(event, threading.Event):
                event.wait(5)
            else:
                yield event
        if last:
            self.release.wait(5)

    async def _async_stream(self, events, last):
        for event in events:
            if isinstance(event, asyncio.Event):
                await event.wait()
            else:
                yield event
        if last:
            await asyncio.sleep(5)

    def posts(self):
        """The bodies of the POSTs of the subscriptions."""
        posts = [request for request in self.requests if request.method == 'POST']
        return [json.loads(request.content) for request in posts if request.url.path == '/api/realtime']

    def refreshes(self):
        """The number of refreshes of the login."""
        return sum(request.url.path.endswith('/auth-refresh') for request in self.requests)


def _sse(name, data):
    # one server sent event as PocketBase writes it
    return f'id:c1\nevent:{name}\ndata:{json.dumps(data)}\n\n'.encode()


def _connect(client_id):
    # the first event of each connection
    return _sse('PB_CONNECT', {'clientId': client_id})


def _change(topic, action, record_id):
    # the event of a changed record
    return _sse(topic, {'action': action, 'record': {'id': record_id, 'title': f'title {record_id}'}})


def _record_delays(channel):
    # record the delay a channel in a thread waits if a try to connect fails
    delays = []
    listen = channel._listen

    def record(stop, http, pending):
        delays.append(channel._delay)
        return listen(stop, http, pending)

    channel._listen = record
    return delays


class _SilentHandler(BaseHTTPRequestHandler):
    """Sends PB_CONNECT on each GET and then nothing, takes each POST with 204."""

    def do_GET(self):
        self.server.gets.append(self.path)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.wfile.write(_connect(f'c{len(self.server.gets)}'))
        self.wfile.flush()
        self.server.release.wait(10)

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def silent_server():
    """The url of a server whose realtime connections fall silent, and its GETs."""
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), _SilentHandler)
    httpd.gets, httpd.release = [], threading.Event()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}', httpd.gets
    httpd.release.set()
    httpd.shutdown()
    httpd.server_close()
    thread.join(5)


@pytest.fixture
def server():
    """A fake realtime api for a channel in a thread."""
    return _Server()


def _client(server):
    # a client logged in to users with the token tok, talking to the fake server
    client = PocketBase(URL, token='tok', transport=httpx.MockTransport(server.handle))
    client.auth_collection = 'users'
    return client


@pytest.fixture
def pb(server):
    """A client logged in with the token tok that talks to the fake server."""
    with _client(server) as client:
        yield client


@pytest.fixture
def realtime(pb, server):
    """A channel in a thread of the client, stopped after the test."""
    channel = Realtime(pb, max_delay=0.04)
    yield channel
    server.release.set()
    channel.stop()


@pytest.fixture
def async_server():
    """A fake realtime api for a channel of asyncio."""
    return _Server(asynchronous=True)


@pytest.fixture
def async_pb(async_server):
    """A client logged in with the token tok that talks to the fake async server."""
    with _client(async_server) as client:
        yield client


# topic_key


def test_topic_key_without_options_is_the_topic():
    """posts/* stays posts/* without options."""
    assert topic_key('posts/*') == 'posts/*'
    assert topic_key('posts/*', {}) == 'posts/*'


def test_topic_key_adds_the_options_as_quoted_json():
    """The options follow as ?options= with their json quoted for the url."""
    assert topic_key('posts/*', EXPAND) == 'posts/*?options=%7B%22query%22%3A%20%7B%22expand%22%3A%20%22owner%22%7D%7D'


# SseParser


def test_sse_parser_gives_the_event_on_its_blank_line():
    """id, event and data lines give (PB_CONNECT, data) on the blank line."""
    parser = SseParser()
    lines = ['id:c1', 'event:PB_CONNECT', 'data:{"clientId":"c1"}']
    assert [parser.feed(line) for line in lines] == [None, None, None]
    assert parser.feed('') == ('PB_CONNECT', {'clientId': 'c1'})


def test_sse_parser_joins_data_lines_and_keeps_text():
    """Two data lines join by a line break, text that is no json stays text."""
    parser = SseParser()
    for line in ['data: one', 'data:two']:
        parser.feed(line)
    assert parser.feed('') == ('message', 'one\ntwo')


def test_sse_parser_skips_comments_and_events_without_data():
    """A comment gives nothing, an event without data is dropped with its name."""
    parser = SseParser()
    lines = [': ping', '', 'event:lost', '', 'data:1']
    assert [parser.feed(line) for line in lines] == [None] * 5
    assert parser.feed('') == ('message', 1)


# RealtimeEvent


def test_realtime_event_reads_action_and_record():
    """The action is update, the record reads its title as attribute."""
    event = RealtimeEvent('posts/*', {'action': 'update', 'record': {'id': 'r1', 'title': 'a'}})
    assert (event.topic, event.action, event.record.title) == ('posts/*', 'update', 'a')
    assert repr(event) == "RealtimeEvent('posts/*', 'update', 'r1')"


# Realtime


def test_realtime_subscribes_on_connect_by_the_login_of_the_client(realtime, server):
    """PB_CONNECT sends clientId and topics with the token, then on_connect runs."""
    server.connections = [[_connect('c1')]]
    calls = []
    realtime.on_connect = lambda: calls.append(realtime.connected.is_set())
    realtime.subscribe('posts/*', print).subscribe('posts/*', print, EXPAND)
    realtime.start()
    assert realtime.connected.wait(5)
    assert server.posts() == [{'clientId': 'c1', 'subscriptions': ['posts/*', topic_key('posts/*', EXPAND)]}]
    assert server.requests[-1].headers['Authorization'] == 'tok'
    assert wait_until(lambda: calls == [True])
    assert realtime.on_connect is not None


def test_realtime_hands_each_event_to_the_callbacks_of_its_topic(realtime, server):
    """Both callbacks of posts/* get the create, posts/r1 the update, no users/*."""
    server.connections = [
        [_connect('c1'), _change('posts/*', 'create', 'r1'), _change('users/*', 'create', 'u1'), _change('posts/r1', 'update', 'r1')]
    ]
    first, second, one = [], [], []
    realtime.subscribe('posts/*', lambda e: first.append((e.action, e.record.id)))
    realtime.subscribe('posts/*', lambda e: second.append((e.action, e.record.id)))
    realtime.subscribe('posts/r1', lambda e: one.append((e.action, e.record.title)))
    realtime.start()
    assert wait_until(lambda: one == [('update', 'title r1')])
    assert first == second == [('create', 'r1')]


def test_realtime_failing_callback_is_logged_and_the_channel_goes_on(realtime, server, caplog):
    """A callback that raises is logged, the next callback and event still come."""
    server.connections = [[_connect('c1'), _change('posts/*', 'create', 'r1'), _change('posts/*', 'create', 'r2')]]
    seen = []

    def fail(event):
        raise ValueError('broken')

    realtime.subscribe('posts/*', fail).subscribe('posts/*', lambda e: seen.append(e.record.id))
    with caplog.at_level(logging.ERROR, logger='tokeo.core.pocketbase.realtime'):
        realtime.start()
        assert wait_until(lambda: seen == ['r1', 'r2'])
    assert sum('callback' in record.message for record in caplog.records) == 2


def test_realtime_changes_while_connected_send_the_subscriptions_again(realtime, server):
    """Only a new or a gone subscription is sent again, a second callback is not."""
    server.connections = [[_connect('c1')]]
    realtime.subscribe('posts/*', print).start()
    assert realtime.connected.wait(5)
    realtime.subscribe('users/*', print)
    realtime.subscribe('users/*', repr)
    realtime.unsubscribe('users/*', print)
    realtime.unsubscribe('users/*', len)
    realtime.unsubscribe('users/*')
    realtime.unsubscribe('nothing/*')
    assert [body['subscriptions'] for body in server.posts()] == [['posts/*'], ['posts/*', 'users/*'], ['posts/*']]


def test_realtime_reconnects_and_subscribes_again_with_the_new_client_id(realtime, server):
    """After the end of the stream c2 is subscribed and on_connect called again."""
    server.connections = [[_connect('c1')], [_connect('c2')]]
    calls = []
    realtime.on_connect = lambda: calls.append('connect')
    realtime.subscribe('posts/*', print).start()
    assert wait_until(lambda: len(calls) == 2)
    assert [body['clientId'] for body in server.posts()] == ['c1', 'c2']


@pytest.mark.parametrize('stable, at_once', [(0, True), (60, False)], ids=['served', 'too_short'])
def test_realtime_connects_again_at_once_only_after_a_connection_that_served(realtime, server, monkeypatch, stable, at_once):
    """
    The server ends c1 cleanly: after STABLE_SECONDS of service the channel
    connects c2 at once, a shorter connection waits the delay of 0.5 seconds.
    """
    monkeypatch.setattr('tokeo.core.pocketbase.realtime.STABLE_SECONDS', stable)
    server.connections = [[_connect('c1')], [_connect('c2')]]
    connects = []
    realtime.on_connect = lambda: connects.append(time.monotonic())
    realtime.subscribe('posts/*', print).start()
    assert wait_until(lambda: len(connects) == 2)
    assert (connects[1] - connects[0] < 0.3) is at_once


def test_realtime_retries_after_a_doubling_delay_up_to_max_delay(realtime, server, caplog):
    """Failed connects wait 0.01, 0.02, 0.04, 0.04 seconds, a connect resets it."""
    server.connections = [503, 503, 503, 503, [_connect('c1')]]
    delays = _record_delays(realtime)
    realtime._delay = 0.01
    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        realtime.subscribe('posts/*', print).start()
        assert realtime.connected.wait(5)
    assert delays == [0.01, 0.02, 0.04, 0.04, 0.04]
    assert realtime._delay == 0.5
    assert sum('connection lost' in record.message for record in caplog.records) == 4


def test_realtime_refused_subscriptions_connect_again(realtime, server, caplog):
    """A POST refused with 403 is logged and the next connection subscribes c2."""
    server.connections = [[_connect('c1')], [_connect('c2')]]
    server.post_status = [403]
    realtime._delay = 0.01
    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        realtime.subscribe('posts/*', print).start()
        assert realtime.connected.wait(5)
    assert [body['clientId'] for body in server.posts()] == ['c1', 'c2']
    assert any('403' in record.message for record in caplog.records)


def test_realtime_silent_connection_counts_as_lost_after_read_timeout(silent_server, caplog):
    """A connection silent for 0.2 seconds, like one to a lost server, reopens."""
    url, gets = silent_server
    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        with PocketBase(url) as client, Realtime(client, read_timeout=0.2) as channel:
            channel.subscribe('posts/*', print)
            assert wait_until(lambda: len(gets) == 2)
    assert any('connection lost' in record.message for record in caplog.records)


def test_realtime_stop_leaves_a_hanging_callback_and_a_new_start_runs_alone(realtime, server):
    """
    A callback hangs on the event r1 while stop() waits only 0.1 seconds,
    a new start connects c2 and gets r3. Once released, the old dispatcher
    hands on no r2, ends and leaves the new channel connected.
    """
    server.connections = [
        [_connect('c1'), _change('posts/*', 'create', 'r1'), _change('posts/*', 'create', 'r2')],
        [_connect('c2'), _change('posts/*', 'create', 'r3')],
    ]
    hang, seen = threading.Event(), []

    def follow(event):
        seen.append(event.record.id)
        if event.record.id == 'r1':
            hang.wait(5)

    realtime.subscribe('posts/*', follow).start()
    assert wait_until(lambda: seen == ['r1'])
    old = realtime._dispatcher
    realtime.stop(timeout=0.1)
    assert old.is_alive()
    realtime.start()
    assert wait_until(lambda: seen == ['r1', 'r3'])
    hang.set()
    old.join(5)
    assert (old.is_alive(), seen, realtime.connected.is_set()) == (False, ['r1', 'r3'], True)
    assert [body['clientId'] for body in server.posts()] == ['c1', 'c2']


def test_realtime_stop_called_by_a_callback_ends_the_thread(realtime, server):
    """A callback calling stop() on r1 ends the thread, r2 reaches no callback."""
    server.connections = [[_connect('c1'), _change('posts/*', 'create', 'r1'), _change('posts/*', 'create', 'r2')]]
    seen, threads = [], []

    def follow(event):
        seen.append(event.record.id)
        threads.append(threading.current_thread())
        # the fake stream has no socket for stop() to shut down
        server.release.set()
        realtime.stop()

    realtime.subscribe('posts/*', follow).start()
    assert wait_until(lambda: threads and not threads[0].is_alive())
    assert (seen, realtime.connected.is_set(), realtime._thread) == (['r1'], False, None)


def test_realtime_refuses_max_pending_below_one(pb):
    """max_pending 0 raises in both channels, the connect mark must fit."""
    for channel in (Realtime, AsyncRealtime):
        with pytest.raises(ValueError, match='at least 1'):
            channel(pb, max_pending=0)


def test_realtime_status_and_topics_follow_the_channel(realtime, server):
    """stopped, live once connected, stopped again; topics keep options."""
    server.connections = [[_connect('c1')]]
    assert realtime.status == 'stopped'
    realtime.subscribe('posts/*', print).subscribe('posts/*', print, EXPAND)
    assert realtime.topics == ['posts/*', topic_key('posts/*', EXPAND)]
    realtime.start()
    assert realtime.connected.wait(5) and (realtime.status, realtime.error) == ('live', None)
    server.release.set()
    realtime.stop()
    assert realtime.status == 'stopped' and set(STATUSES) == {'live', 'connecting', 'stopped'}


def test_realtime_confirms_the_login_before_it_subscribes(realtime, server):
    """The refresh of the login goes before the POST of the subscriptions."""
    server.connections = [[_connect('c1')]]
    realtime.subscribe('posts/*', print).start()
    assert realtime.connected.wait(5)
    paths = [request.url.path for request in server.requests if request.method == 'POST']
    assert paths == ['/api/collections/users/auth-refresh', '/api/realtime']


def test_realtime_refused_login_shows_an_error_instead_of_a_silent_guest(realtime, server, caplog):
    """A refresh refused with 401 sends no subscriptions, the 401 shows."""
    server.connections = [[_connect('c1')], [_connect('c2')]]
    server.refresh_status = [401, 401]
    realtime._delay = 0.01
    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        realtime.subscribe('posts/*', print).start()
        assert wait_until(lambda: server.refreshes() == 2)
    assert (server.posts(), realtime.status, '401' in realtime.error) == ([], 'connecting', True)


def test_realtime_checks_the_login_while_connected_and_subscribes_again_after_a_new_login(realtime, server, pb):
    """
    Every 0.1 seconds the hook of the client confirms the login; once it
    logged in anew, the subscriptions go again on the same connection, as
    PocketBase dropped the old login of the connection.
    """
    server.connections = [[_connect('c1')]]
    calls = []

    def hook():
        calls.append(1)
        if len(calls) == 3:
            # a new login, e.g. after the password of the identity changed
            pb.logins += 1

    pb.before_request = hook
    realtime.auth_check = 0.1
    realtime.subscribe('posts/*', print).start()
    assert wait_until(lambda: len(server.posts()) == 2)
    assert [body['clientId'] for body in server.posts()] == ['c1', 'c1']


def test_realtime_lost_login_while_connected_hangs_up_and_shows_the_error(silent_server, caplog):
    """
    The login holds at connect but not at the check 0.1 seconds later: the
    channel logs the error, hangs up the real connection and connects again,
    each try refused alike, its status stays connecting.
    """
    url, gets = silent_server
    calls = []
    test_thread = threading.current_thread()

    def hook():
        # only the calls of the channel count: subscribe in the test thread
        # confirms the login too, at a moment the test does not control
        if threading.current_thread() is test_thread:
            return
        calls.append(1)
        if len(calls) > 1:
            raise TokeoPocketBaseAuthError('login refused')

    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        with PocketBase(url, before_request=hook) as client, Realtime(client, auth_check=0.1, max_delay=0.05) as channel:
            channel.subscribe('posts/*', print)
            assert wait_until(lambda: len(gets) >= 2 and channel.status == 'connecting')
            assert 'login refused' in channel.error
    assert any('login not confirmed' in record.message for record in caplog.records)


def test_realtime_hangs_up_when_the_callbacks_fall_behind_and_connects_once_they_caught_up(pb, server, caplog):
    """
    With max_pending 2 a callback hangs on r1 while r2 to r5 arrive: the
    channel hangs up at r4 and connects no more until the callback ends;
    then it hands on the queued r2 and r3, connects c2 after max_delay and
    calls on_connect again.
    """
    hanging = threading.Event()
    server.connections = [
        [_connect('c1'), _change('posts/*', 'create', 'r1'), hanging, *[_change('posts/*', 'create', f'r{n}') for n in range(2, 6)]],
        [_connect('c2'), _change('posts/*', 'create', 'r6')],
    ]
    release, seen, connects = threading.Event(), [], []

    def follow(event):
        seen.append(event.record.id)
        if event.record.id == 'r1':
            hanging.set()
            release.wait(5)

    channel = Realtime(pb, max_delay=0.05, max_pending=2).subscribe('posts/*', follow)
    channel.on_connect = lambda: connects.append(1)
    try:
        with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
            channel.start()
            assert wait_until(lambda: 'events wait' in (channel.error or ''))
        assert (len(server.posts()), channel.status) == (1, 'connecting')
        release.set()
        assert wait_until(lambda: seen == ['r1', 'r2', 'r3', 'r6'])
        assert (len(connects), [body['clientId'] for body in server.posts()]) == (2, ['c1', 'c2'])
    finally:
        release.set()
        server.release.set()
        channel.stop()


def test_realtime_as_context_manager_runs_one_thread_until_stop(pb, server):
    """A second start keeps the thread, the end of the with stops it at once."""
    server.connections = [[_connect('c1')]]
    with Realtime(pb) as channel:
        thread = channel._thread
        assert channel.start()._thread is thread
        assert channel.connected.wait(5)
        # the fake stream has no socket to shut down, it ends by itself
        server.release.set()
    assert (thread.is_alive(), channel.connected.is_set()) == (False, False)
    channel.stop()


# AsyncRealtime


def test_async_realtime_subscribes_by_the_hook_and_awaits_coroutines(async_pb, async_server):
    """The hook sets the token of the POST, function and coroutine get events."""
    async_server.connections = [[_connect('c1'), _change('posts/*', 'delete', 'r1')]]
    hooks, seen = [], []

    def hook():
        hooks.append(threading.current_thread() is not threading.main_thread())
        async_pb.token = 'fresh'

    async def later(event):
        seen.append(('coroutine', event.action))

    async def connected():
        seen.append('connect')

    async_pb.before_request = hook

    async def go():
        realtime = AsyncRealtime(async_pb)
        realtime.on_connect = connected
        realtime.subscribe('posts/*', lambda e: seen.append(('function', e.action))).subscribe('posts/*', later)
        task = asyncio.create_task(realtime.run())
        assert await wait_until_async(lambda: len(seen) == 3)
        await realtime.stop()
        assert task.done() and not realtime.connected.is_set()

    asyncio.run(go())
    assert seen == ['connect', ('function', 'delete'), ('coroutine', 'delete')]
    assert hooks == [True]
    assert async_server.posts() == [{'clientId': 'c1', 'subscriptions': ['posts/*']}]
    assert async_server.requests[-1].headers['Authorization'] == 'fresh'


def test_async_realtime_changes_while_connected_send_the_subscriptions_again(async_pb, async_server, caplog):
    """A new and a gone subscription are sent again, a refused one is logged."""
    async_server.connections = [[_connect('c1')]]
    async_server.post_status = [204, 204, 500]

    async def go():
        realtime = AsyncRealtime(async_pb).subscribe('posts/*', print)
        asyncio.create_task(realtime.run())
        assert await wait_until_async(realtime.connected.is_set)
        realtime.subscribe('users/*', print)
        assert await wait_until_async(lambda: len(async_server.posts()) == 2)
        realtime.unsubscribe('users/*')
        assert await wait_until_async(lambda: len(async_server.posts()) == 3)
        await realtime.stop()

    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        asyncio.run(go())
    assert [body['subscriptions'] for body in async_server.posts()] == [['posts/*'], ['posts/*', 'users/*'], ['posts/*']]
    assert any('not sent' in record.message and '500' in record.message for record in caplog.records)


def test_async_realtime_failing_callback_is_logged_and_the_channel_goes_on(async_pb, async_server, caplog):
    """A coroutine that raises is logged, the next event still comes."""
    async_server.connections = [[_connect('c1'), _change('posts/*', 'create', 'r1'), _change('posts/*', 'create', 'r2')]]
    seen = []

    async def fail(event):
        seen.append(event.record.id)
        raise ValueError('broken')

    async def go():
        realtime = AsyncRealtime(async_pb).subscribe('posts/*', fail)
        asyncio.create_task(realtime.run())
        assert await wait_until_async(lambda: seen == ['r1', 'r2'])
        await realtime.stop()

    with caplog.at_level(logging.ERROR, logger='tokeo.core.pocketbase.realtime'):
        asyncio.run(go())
    assert sum('callback' in record.message for record in caplog.records) == 2


@pytest.mark.parametrize('stable, at_once', [(0, True), (60, False)], ids=['served', 'too_short'])
def test_async_realtime_connects_again_at_once_only_after_a_connection_that_served(async_pb, async_server, monkeypatch, stable, at_once):
    """After STABLE_SECONDS c2 comes at once, else after 0.5 seconds."""
    monkeypatch.setattr('tokeo.core.pocketbase.realtime.STABLE_SECONDS', stable)
    async_server.connections = [[_connect('c1')], [_connect('c2')]]
    connects = []

    async def go():
        realtime = AsyncRealtime(async_pb).subscribe('posts/*', print)
        realtime.on_connect = lambda: connects.append(time.monotonic())
        asyncio.create_task(realtime.run())
        assert await wait_until_async(lambda: len(connects) == 2)
        await realtime.stop()

    asyncio.run(go())
    assert (connects[1] - connects[0] < 0.3) is at_once


def test_async_realtime_retries_after_a_doubling_delay_and_subscribes_again(async_pb, async_server, caplog):
    """A failed connect and a refused POST are logged, the third subscribes c2."""
    async_server.connections = [503, [_connect('c1')], [_connect('c2')]]
    async_server.post_status = [403]
    calls = []

    async def go():
        realtime = AsyncRealtime(async_pb, max_delay=0.02).subscribe('posts/*', print)
        realtime.on_connect = lambda: calls.append(realtime._delay)
        realtime._delay = 0.01
        asyncio.create_task(realtime.run())
        assert await wait_until_async(lambda: calls)
        await realtime.stop()

    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        asyncio.run(go())
    assert calls == [0.5]
    assert [body['clientId'] for body in async_server.posts()] == ['c1', 'c2']
    assert sum('connection lost' in record.message for record in caplog.records) == 2


def test_async_realtime_silent_connection_counts_as_lost_after_read_timeout(silent_server, caplog):
    """A connection silent for 0.2 seconds is opened again, stop() ends the task."""
    url, gets = silent_server

    async def go():
        with PocketBase(url) as client:
            realtime = AsyncRealtime(client, read_timeout=0.2).subscribe('posts/*', print)
            task = asyncio.create_task(realtime.run())
            assert await wait_until_async(lambda: len(gets) == 2)
            await realtime.stop()
            assert task.done()

    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        asyncio.run(go())
    assert any('connection lost' in record.message for record in caplog.records)


def test_async_realtime_runs_once_at_a_time(async_pb, async_server):
    """A second run() while the first runs raises, the first goes on."""
    async_server.connections = [[_connect('c1')]]

    async def go():
        realtime = AsyncRealtime(async_pb)
        task = asyncio.create_task(realtime.run())
        assert await wait_until_async(realtime.connected.is_set)
        with pytest.raises(TokeoPocketBaseError, match='runs already'):
            await realtime.run()
        assert not task.done()
        await realtime.stop()

    asyncio.run(go())


def test_async_realtime_stop_cancels_the_pending_sends(async_pb, async_server):
    """A subscription changed just before stop() leaves no task behind."""
    async_server.connections = [[_connect('c1')]]

    async def go():
        realtime = AsyncRealtime(async_pb)
        asyncio.create_task(realtime.run())
        assert await wait_until_async(realtime.connected.is_set)
        realtime.subscribe('users/*', print)
        sends = set(realtime._sends)
        await realtime.stop()
        assert len(sends) == 1 and all(task.done() for task in sends)
        assert realtime._sends == set()

    asyncio.run(go())


def test_async_realtime_flush_waits_for_the_changed_subscriptions(async_pb, async_server):
    """After flush() the new subscription is sent; status and topics follow."""
    async_server.connections = [[_connect('c1')]]

    async def go():
        realtime = AsyncRealtime(async_pb).subscribe('posts/*', print)
        assert realtime.status == 'stopped'
        asyncio.create_task(realtime.run())
        assert await wait_until_async(realtime.connected.is_set)
        assert realtime.status == 'live'
        realtime.subscribe('users/*', print)
        await realtime.flush()
        assert [body['subscriptions'] for body in async_server.posts()] == [['posts/*'], ['posts/*', 'users/*']]
        assert realtime.topics == ['posts/*', 'users/*']
        await realtime.stop()
        assert realtime.status == 'stopped'

    asyncio.run(go())


def test_async_realtime_checks_the_login_and_subscribes_again_after_a_new_login(async_pb, async_server):
    """The hook confirms every 0.1 s; after a new login c1 subscribes again."""
    async_server.connections = [[_connect('c1')]]
    calls = []

    def hook():
        calls.append(1)
        if len(calls) == 3:
            async_pb.logins += 1

    async_pb.before_request = hook

    async def go():
        realtime = AsyncRealtime(async_pb, auth_check=0.1).subscribe('posts/*', print)
        asyncio.create_task(realtime.run())
        assert await wait_until_async(lambda: len(async_server.posts()) == 2)
        await realtime.stop()

    asyncio.run(go())
    assert [body['clientId'] for body in async_server.posts()] == ['c1', 'c1']


def test_async_realtime_lost_login_hangs_up_and_shows_the_error(async_pb, async_server, caplog):
    """A failed check after connect hangs up, logs and shows the error."""
    async_server.connections = [[_connect('c1')]]
    calls = []

    def hook():
        calls.append(1)
        if len(calls) > 1:
            raise TokeoPocketBaseAuthError('login refused')

    async_pb.before_request = hook

    async def go():
        realtime = AsyncRealtime(async_pb, auth_check=0.1, max_delay=0.05).subscribe('posts/*', print)
        asyncio.create_task(realtime.run())
        assert await wait_until_async(lambda: realtime.error and 'login refused' in realtime.error)
        assert await wait_until_async(lambda: sum(request.method == 'GET' for request in async_server.requests) >= 2)
        assert realtime.status == 'connecting'
        await realtime.stop()

    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        asyncio.run(go())
    assert any('login not confirmed' in record.message for record in caplog.records)


def test_async_realtime_hangs_up_when_the_callbacks_fall_behind(async_pb, async_server, caplog):
    """
    With max_pending 2 a coroutine hangs on r1 while r2 to r5 arrive: the
    channel hangs up at r4, after the release it hands on r2 and r3 and
    connects c2 with r6.
    """
    seen, connects = [], []

    async def go():
        hanging, release = asyncio.Event(), asyncio.Event()
        async_server.connections = [
            [_connect('c1'), _change('posts/*', 'create', 'r1'), hanging, *[_change('posts/*', 'create', f'r{n}') for n in range(2, 6)]],
            [_connect('c2'), _change('posts/*', 'create', 'r6')],
        ]

        async def follow(event):
            seen.append(event.record.id)
            if event.record.id == 'r1':
                hanging.set()
                await release.wait()

        realtime = AsyncRealtime(async_pb, max_delay=0.05, max_pending=2).subscribe('posts/*', follow)
        realtime.on_connect = lambda: connects.append(1)
        asyncio.create_task(realtime.run())
        assert await wait_until_async(lambda: 'events wait' in (realtime.error or ''))
        assert len(async_server.posts()) == 1
        release.set()
        assert await wait_until_async(lambda: seen == ['r1', 'r2', 'r3', 'r6'])
        await realtime.stop()

    with caplog.at_level(logging.WARNING, logger='tokeo.core.pocketbase.realtime'):
        asyncio.run(go())
    assert (len(connects), [body['clientId'] for body in async_server.posts()]) == (2, ['c1', 'c2'])


def test_confirm_login_takes_the_hook_else_a_refresh_else_nothing(pb, server):
    """A hook runs alone, else the token is refreshed, a guest asks nothing."""
    calls = []
    pb.before_request = lambda: calls.append('hook')
    confirm_login(pb)
    pb.before_request = None
    confirm_login(pb)
    with PocketBase(URL, transport=httpx.MockTransport(server.handle)) as guest:
        confirm_login(guest)
    assert (calls, server.refreshes()) == (['hook'], 1)


def test_async_realtime_stop_without_run_does_nothing(async_pb):
    """stop() before run() returns at once."""
    asyncio.run(AsyncRealtime(async_pb).stop())
