"""
Tests for tokeo.core.pocketbase against a real PocketBase server. The tests
start the binary named by ```TOKEO_TEST_POCKETBASE_BIN``` once for the
module on a free port with a fresh data dir, create a superuser, a user, the
collection posts and switch the batch api on; without the binary they are
skipped.

They cover what only a real server shows: the filter values that try to
break out of their string, the upload of files with the fields as
```@jsonPayload```, expand, a batch that is undone as a whole, login,
refresh, impersonation and the realtime events, in threads and for asyncio,
by topic, options and the rules of the collection; a callback that hangs,
and logins that expire, change their password or are deleted while a
channel runs. A second server runs with the hook file of tokeo-pocketbase,
```realtime.pb.js.md``` copied as it is, set to end realtime connections after
1 second without events and after 3 seconds in all.
"""

import os
import time
import asyncio
import threading
import subprocess

import pytest
from tokeo.core.pocketbase import AuthKeeper, PocketBase, TokeoPocketBaseAuthError, TokeoPocketBaseError
from tokeo.core.pocketbase.realtime import AsyncRealtime, Realtime
from tests.utils import ROOT, free_port, wait_until, wait_until_async
from tests.utils.process import SubProcess

#: The pocketbase binary of the tests, e.g. of version 0.40.6
POCKETBASE_BIN = os.environ.get('TOKEO_TEST_POCKETBASE_BIN', '')

pytestmark = pytest.mark.skipif(
    not os.path.isfile(POCKETBASE_BIN),
    reason='needs the pocketbase binary (set TOKEO_TEST_POCKETBASE_BIN)',
)

SUPERUSER = ('admin@tokeo.test', 'admin-pass-123')
USER = ('anna@tokeo.test', 'anna-pass-123')

# titles that try to leave their string in a filter
TRICKY = ["it's", 'back\\', 'q"uote', "x\\' || id != '", '{:t}']


def _sockets():
    # the number of open sockets of this process
    count = 0
    for fd in os.listdir('/proc/self/fd'):
        try:
            count += os.readlink(f'/proc/self/fd/{fd}').startswith('socket:')
        except OSError:
            pass
    return count


def _user(su, rando):
    # a user of its own for a test that changes or deletes it
    return su.collection('users').create({'email': f'{rando}@tokeo.test', 'password': USER[1], 'passwordConfirm': USER[1]})


def _kept(su, user, check_seconds):
    # a client acting as the user, its login kept by a new impersonation
    client = su.impersonate('users', user.id)
    client.before_request = AuthKeeper(client, lambda: su.impersonate('users', user.id, client=client), check_seconds=check_seconds)
    return client


def _healthy(url):
    # whether the server answers its health check
    try:
        with PocketBase(url) as pb:
            return pb.health()['code'] == 200
    except TokeoPocketBaseError:
        return False


@pytest.fixture(scope='module')
def live(tmp_path_factory):
    """A running PocketBase with superuser, user, posts and the batch api on."""
    data = str(tmp_path_factory.mktemp('pocketbase'))
    subprocess.run([POCKETBASE_BIN, 'superuser', 'upsert', *SUPERUSER, '--dir', data], check=True, capture_output=True)
    url = f'http://127.0.0.1:{free_port()}'
    with SubProcess.run(POCKETBASE_BIN, 'serve', '--dir', data, '--http', url.removeprefix('http://'), timeout=15) as process:
        process.wait_for(lambda: _healthy(url))
        with PocketBase(url) as su:
            su.auth_with_password('_superusers', *SUPERUSER)
            rule = '@request.auth.id != ""'
            su.send(
                'POST',
                '/api/collections',
                json={
                    'name': 'posts',
                    'type': 'base',
                    **{f'{action}Rule': rule for action in ('list', 'view', 'create', 'update', 'delete')},
                    'fields': [
                        {'name': 'title', 'type': 'text'},
                        {'name': 'views', 'type': 'number'},
                        {'name': 'meta', 'type': 'json'},
                        {'name': 'doc', 'type': 'file', 'maxSelect': 2, 'maxSize': 100000},
                        {'name': 'owner', 'type': 'relation', 'collectionId': '_pb_users_auth_', 'maxSelect': 1},
                    ],
                },
            )
            su.send('PATCH', '/api/settings', json={'batch': {'enabled': True, 'maxRequests': 50, 'timeout': 3}})
            user = su.collection('users').create({'email': USER[0], 'password': USER[1], 'passwordConfirm': USER[1], 'name': 'Anna'})
        yield url, user.id


@pytest.fixture
def pb(live):
    """A client logged in as the user anna."""
    with PocketBase(live[0]) as client:
        client.auth_with_password('users', *USER)
        yield client


@pytest.fixture
def su(live):
    """A client logged in as superuser."""
    with PocketBase(live[0]) as client:
        client.auth_with_password('_superusers', *SUPERUSER)
        yield client


def test_live_filter_finds_exactly_the_tricky_title(pb):
    """Each title that tries to break out of its string finds only itself."""
    posts = pb.collection('posts')
    for title in TRICKY:
        posts.create({'title': title})
    for title in TRICKY:
        found = posts.get_list(filter=pb.filter('title = {:t}', {'t': title}))
        assert [record.title for record in found.items] == [title]


def test_live_records_round_trip_with_expand(pb, live):
    """A record is created, read with its expanded owner, changed and deleted."""
    posts = pb.collection('posts')
    post = posts.create({'title': 'round', 'views': 1, 'meta': {'a': [1, 2]}, 'owner': live[1]})
    read = posts.get_one(post.id, expand='owner')
    assert (read.title, read.meta, read.expand.owner.name) == ('round', {'a': [1, 2]}, 'Anna')
    assert posts.update(post.id, {'views': 2}).views == 2
    assert posts.delete(post.id) is True
    with pytest.raises(TokeoPocketBaseError) as caught:
        posts.get_one(post.id)
    assert caught.value.status == 404


def test_live_upload_takes_the_fields_as_json_payload(pb):
    """Files go along with json fields, doc+ appends one more file."""
    posts = pb.collection('posts')
    post = posts.create({'title': 'files', 'meta': {'nested': True}}, files=[('doc', ('a.txt', b'hello', 'text/plain'))])
    assert (post.meta, len(post.doc)) == ({'nested': True}, 1)
    post = posts.update(post.id, {'title': 'more'}, files=[('doc+', ('b.txt', b'world', 'text/plain'))])
    assert (post.title, len(post.doc)) == ('more', 2)


def test_live_get_full_list_and_get_first(pb):
    """get_full_list pages through all, get_first finds one or raises 404."""
    posts = pb.collection('posts')
    for n in range(5):
        posts.create({'title': f'page-{n}'})
    titles = [record.title for record in posts.get_full_list(per_page=2, filter="title ~ 'page-'", sort='title')]
    assert titles == [f'page-{n}' for n in range(5)]
    assert posts.get_first(pb.filter('title = {:t}', {'t': 'page-3'})).title == 'page-3'
    with pytest.raises(TokeoPocketBaseError) as caught:
        posts.get_first("title = 'nothing'")
    assert caught.value.status == 404


def test_live_batch_is_all_or_nothing(pb):
    """A batch with a failing change creates nothing, a valid one does all."""
    posts = pb.collection('posts')
    with pytest.raises(TokeoPocketBaseError) as caught:
        pb.batch().create('posts', {'title': 'batch-lost'}).update('posts', 'nope12345678901', {'title': 'x'}).send()
    assert caught.value.data['requests']['1']['response']['status'] == 404
    assert posts.get_list(filter="title = 'batch-lost'").total_items == 0
    created, upserted = (
        pb.batch().create('posts', {'title': 'batch-a'}).upsert('posts', {'id': 'batchupsert0001', 'title': 'batch-b'}).send()
    )
    assert (created.title, upserted.id) == ('batch-a', 'batchupsert0001')


def test_live_refresh_and_wrong_password(pb):
    """A refresh keeps a valid token, a wrong password raises an auth error."""
    pb.auth_refresh()
    assert pb.token_valid(60)
    with pytest.raises(TokeoPocketBaseAuthError) as caught:
        pb.auth_with_password('users', USER[0], 'wrong')
    assert caught.value.status == 400


def test_live_impersonate_acts_as_the_user(su, pb, live):
    """A superuser gets a client of the user, the user cannot impersonate."""
    client = su.impersonate('users', live[1], duration=60)
    try:
        assert (client.auth_record.email, client.token_valid(30), client.token_valid(120)) == (USER[0], True, False)
        assert client.collection('posts').create({'title': 'as anna', 'owner': live[1]}).owner == live[1]
    finally:
        client.close()
    with pytest.raises(TokeoPocketBaseAuthError) as caught:
        pb.impersonate('users', live[1])
    assert caught.value.status == 403


def test_live_health(live):
    """health of the running server answers code 200."""
    with PocketBase(live[0]) as client:
        assert client.health()['code'] == 200


# Realtime


def test_live_realtime_events_by_topic_options_and_rules(pb, live):
    """
    anna follows all posts, all posts with the owner expanded and, once
    created, one post; an anonymous channel follows all posts too. Create,
    update and delete reach all posts, the owner comes expanded, the one
    post gets update and delete. After the unsubscribe of all posts a new
    post reaches only the expanded subscription, the anonymous channel gets
    nothing by the list rule.
    """
    events, expanded, one, anonymous = [], [], [], []
    posts = pb.collection('posts')
    with PocketBase(live[0]) as guest, Realtime(pb) as channel, Realtime(guest) as guest_channel:
        channel.subscribe('posts/*', lambda e: events.append((e.action, e.record.title)))
        channel.subscribe('posts/*', lambda e: expanded.append(e.record.expand.owner.name), {'query': {'expand': 'owner'}})
        guest_channel.subscribe('posts/*', anonymous.append)
        assert channel.connected.wait(5) and guest_channel.connected.wait(5)
        post = posts.create({'title': 'rt', 'owner': live[1]})
        channel.subscribe(f'posts/{post.id}', lambda e: one.append(e.action))
        posts.update(post.id, {'title': 'rt2'})
        posts.delete(post.id)
        assert wait_until(lambda: events == [('create', 'rt'), ('update', 'rt2'), ('delete', 'rt2')])
        assert wait_until(lambda: one == ['update', 'delete'])
        channel.unsubscribe('posts/*')
        posts.create({'title': 'rt3', 'owner': live[1]})
        assert wait_until(lambda: expanded == ['Anna'] * 4)
    assert (len(events), anonymous) == (3, [])


def test_live_realtime_stop_ends_a_waiting_channel_at_once(pb):
    """stop() of a channel waiting for events ends its thread within a second."""
    channel = Realtime(pb).subscribe('posts/*', print).start()
    assert channel.connected.wait(5)
    thread, started = channel._thread, time.monotonic()
    channel.stop()
    assert (thread.is_alive(), time.monotonic() - started < 1) == (False, True)


@pytest.mark.skipif(not os.path.isdir('/proc/self/fd'), reason='needs /proc to count the open sockets')
def test_live_realtime_start_and_stop_leave_no_thread_or_socket(pb):
    """
    20 channels in a thread and 20 for asyncio connect, subscribe and stop
    one after the other; afterwards as many threads and sockets are open as
    before.
    """
    # the pooled connection of the client is open before counting
    pb.health()
    before = (threading.active_count(), _sockets())
    for _ in range(20):
        with Realtime(pb) as channel:
            channel.subscribe('posts/*', print)
            assert channel.connected.wait(5)

    async def go():
        for _ in range(20):
            realtime = AsyncRealtime(pb).subscribe('posts/*', print)
            asyncio.create_task(realtime.run())
            assert await wait_until_async(realtime.connected.is_set)
            await realtime.stop()

    asyncio.run(go())
    assert wait_until(lambda: (threading.active_count(), _sockets()) == before)


def test_live_async_realtime_awaits_a_coroutine_for_each_event(pb):
    """A coroutine gets create, update and delete, stop() ends the task."""
    posts = pb.collection('posts')
    events = []

    async def follow(event):
        events.append((event.action, event.record.title))

    async def go():
        realtime = AsyncRealtime(pb).subscribe('posts/*', follow)
        task = asyncio.create_task(realtime.run())
        assert await wait_until_async(realtime.connected.is_set)
        post = await asyncio.to_thread(posts.create, {'title': 'async'})
        await asyncio.to_thread(posts.update, post.id, {'title': 'async2'})
        await asyncio.to_thread(posts.delete, post.id)
        assert await wait_until_async(lambda: len(events) == 3)
        await realtime.stop()
        assert task.done()

    asyncio.run(go())
    assert events == [('create', 'async'), ('update', 'async2'), ('delete', 'async2')]


def test_live_realtime_hanging_callback_hangs_up_and_catches_up_after(pb, su):
    """
    A callback hangs on the first of 300 posts with max_pending 50: the
    channel hangs up instead of letting PocketBase hold the events; once
    released it hands on what it queued, connects again and gets the next
    post.
    """
    release, seen, connects = threading.Event(), [], []

    def follow(event):
        seen.append(event.record.title)
        if len(seen) == 1:
            release.wait(30)

    posts = su.collection('posts')
    with Realtime(pb, max_pending=50, max_delay=0.5) as channel:
        channel.on_connect = lambda: connects.append(1)
        channel.subscribe('posts/*', follow)
        assert channel.connected.wait(5)
        for n in range(300):
            posts.create({'title': f'hang-{n}'})
        assert wait_until(lambda: 'events wait' in (channel.error or ''))
        assert channel.status == 'connecting'
        release.set()
        assert wait_until(lambda: channel.status == 'live' and len(connects) == 2)
        posts.create({'title': 'after'})
        assert wait_until(lambda: seen[-1] == 'after')
    assert 1 + 50 <= len(seen) - 1 < 300


def test_live_realtime_expired_token_without_hook_shows_the_error(su, rando):
    """An expired token is refused at the check, no silent guest channel."""
    user = _user(su, rando)
    client = su.impersonate('users', user.id, duration=1)
    try:
        time.sleep(1.5)
        with Realtime(client, max_delay=0.2) as channel:
            channel.subscribe('posts/*', print)
            assert wait_until(lambda: channel.error and '401' in channel.error)
            assert channel.status == 'connecting'
    finally:
        client.close()


def test_live_realtime_changed_password_renews_the_login_and_the_events_go_on(su, rando):
    """
    The password of the user changes while its channel runs: PocketBase
    drops the login of the connection, the check 0.2 seconds later is
    refused, a new impersonation logs in and sends the subscriptions again,
    so the next post arrives.
    """
    user = _user(su, rando)
    client = _kept(su, user, 0.2)
    seen = []
    try:
        with Realtime(client, auth_check=0.2) as channel:
            channel.subscribe('posts/*', lambda event: seen.append(event.record.title))
            assert channel.connected.wait(5)
            su.collection('users').update(user.id, {'password': 'other-pass-456', 'passwordConfirm': 'other-pass-456'})
            assert wait_until(lambda: client.logins == 2)
            su.collection('posts').create({'title': 'after-password'})
            assert wait_until(lambda: seen == ['after-password'])
            assert channel.status == 'live'
    finally:
        client.close()


def test_live_realtime_deleted_user_shows_the_error(su, rando):
    """A deleted user fails the new impersonation, the channel shows the 404."""
    user = _user(su, rando)
    client = _kept(su, user, 0.2)
    try:
        with Realtime(client, auth_check=0.2, max_delay=0.2) as channel:
            channel.subscribe('posts/*', print)
            assert channel.connected.wait(5)
            su.collection('users').delete(user.id)
            assert wait_until(lambda: channel.status == 'connecting' and '404' in (channel.error or ''))
    finally:
        client.close()


def test_live_async_realtime_hanging_coroutine_hangs_up_and_catches_up_after(pb, su):
    """A hanging coroutine with max_pending 50 hangs up, then it catches up."""
    seen = []
    posts = su.collection('posts')

    async def go():
        release = asyncio.Event()

        async def follow(event):
            seen.append(event.record.title)
            if len(seen) == 1:
                await release.wait()

        realtime = AsyncRealtime(pb, max_pending=50, max_delay=0.5).subscribe('posts/*', follow)
        asyncio.create_task(realtime.run())
        assert await wait_until_async(realtime.connected.is_set)
        for n in range(300):
            await asyncio.to_thread(posts.create, {'title': f'ahang-{n}'})
        assert await wait_until_async(lambda: 'events wait' in (realtime.error or ''))
        release.set()
        assert await wait_until_async(lambda: realtime.status == 'live')
        await asyncio.to_thread(posts.create, {'title': 'aafter'})
        assert await wait_until_async(lambda: seen[-1] == 'aafter')
        await realtime.stop()

    asyncio.run(go())


# the hook file of tokeo-pocketbase with short realtime connections


#: The hook file of tokeo-pocketbase, code only, shown by its POCKETBASE.md
HOOK = os.path.join(ROOT, 'tokeo-pocketbase', 'tokeo', 'core', 'pocketbase', 'realtime.pb.js.md')


def test_live_hook_file_is_shown_by_the_guide_as_javascript():
    """POCKETBASE.md includes realtime.pb.js.md as a javascript code block."""
    guide = open(os.path.join(os.path.dirname(HOOK), 'POCKETBASE.md')).read()
    assert '.. include:: ./realtime.pb.js.md\n   :code: javascript\n' in guide


@pytest.fixture(scope='module')
def short(tmp_path_factory):
    """A PocketBase by the hook file as it is: 1 s idle, 3 s in all, ticks."""
    data = str(tmp_path_factory.mktemp('pocketbase_short'))
    subprocess.run([POCKETBASE_BIN, 'superuser', 'upsert', *SUPERUSER, '--dir', data], check=True, capture_output=True)
    url = f'http://127.0.0.1:{free_port()}'
    hooks = tmp_path_factory.mktemp('pb_hooks')
    (hooks / 'realtime.pb.js').write_text(open(HOOK).read())
    env = dict(PB_REALTIME_IDLE_SECONDS='1', PB_REALTIME_MAX_SECONDS='3')
    args = [POCKETBASE_BIN, 'serve', '--dir', data, '--hooksDir', str(hooks), '--hooksWatch=false', '--http', url.removeprefix('http://')]
    with SubProcess.run(*args, env=env, timeout=15) as process:
        process.wait_for(lambda: _healthy(url))
        with PocketBase(url) as su:
            su.auth_with_password('_superusers', *SUPERUSER)
            su.send(
                'POST',
                '/api/collections',
                json={'name': 'ticks', 'type': 'base', 'listRule': '', 'viewRule': '', 'fields': [{'name': 'n', 'type': 'number'}]},
            )
        yield url


def test_live_hook_file_ends_silent_connections_and_the_channel_comes_back(short, monkeypatch):
    """Without events each connection ends after 1 s, the channel comes back."""
    monkeypatch.setattr('tokeo.core.pocketbase.realtime.STABLE_SECONDS', 0.5)
    connects = []
    with PocketBase(short) as client, Realtime(client) as channel:
        channel.on_connect = lambda: connects.append(time.monotonic())
        channel.subscribe('ticks/*', print)
        assert wait_until(lambda: len(connects) >= 4, timeout=10)
    gaps = [b - a for a, b in zip(connects, connects[1:])]
    assert all(0.9 < gap < 1.5 for gap in gaps)


def test_live_hook_file_ends_busy_connections_and_events_go_on_at_once(short, monkeypatch):
    """
    With a tick every 0.1 seconds the server ends each connection after 3
    seconds; the channel connects again at once, so no two ticks are more
    than 0.4 seconds apart, as the delay of 0.5 seconds would make them.
    """
    monkeypatch.setattr('tokeo.core.pocketbase.realtime.STABLE_SECONDS', 0.5)
    connects, ticks = [], []
    with PocketBase(short) as su, PocketBase(short) as client:
        su.auth_with_password('_superusers', *SUPERUSER)
        with Realtime(client) as channel:
            channel.on_connect = lambda: connects.append(1)
            channel.subscribe('ticks/*', lambda event: ticks.append(time.monotonic()))
            assert channel.connected.wait(5)
            for n in range(70):
                su.collection('ticks').create({'n': n})
                time.sleep(0.1)
            assert wait_until(lambda: len(connects) >= 3)
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) < 0.4 and len(ticks) >= 60
