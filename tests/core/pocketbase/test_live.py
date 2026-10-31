"""
Tests for tokeo.core.pocketbase against a real PocketBase server. The tests
start the binary named by ```TOKEO_TEST_POCKETBASE_BIN``` once for the
module on a free port with a fresh data dir, create a superuser, a user, the
collection posts and switch the batch api on; without the binary they are
skipped.

They cover what only a real server shows: the filter values that try to
break out of their string, the upload of files with the fields as
```@jsonPayload```, expand, a batch that is undone as a whole, login,
refresh and impersonation.
"""

import os
import socket
import subprocess

import pytest
from tokeo.core.pocketbase import PocketBase, TokeoPocketBaseAuthError, TokeoPocketBaseError
from tests.utils import wait_until

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


def _free_port():
    # a port nobody listens on right now
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


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
    url = f'http://127.0.0.1:{_free_port()}'
    process = subprocess.Popen(
        [POCKETBASE_BIN, 'serve', '--dir', data, '--http', url.removeprefix('http://')],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_until(lambda: _healthy(url), timeout=15)
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
    finally:
        process.terminate()
        process.wait(10)


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
