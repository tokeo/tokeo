"""
Tests for tokeo.ext.pocketbase without a PocketBase server: the setup of the
client from the config (url, tls, connection limit), the requests the CRUD
methods send (path, method, query, body) and what they return, the errors
they pass on, the lazy service login with its token refresh and re-login,
and close.

Every pocketbase client a test builds talks to a fake server, an
httpx.MockTransport that answers like PocketBase and records each request.
"""

import json
import ssl
import base64
import itertools
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import certifi
import httpx
import pocketbase
import pytest
from cement.utils.misc import init_defaults
from pocketbase.errors import ClientResponseError
from pocketbase.models.list_result import ListResult
from pocketbase.models.record import Record
from pocketbase.services.record_service import RecordService
from tokeo.core.exc import TokeoError
from tokeo.ext.pocketbase import TokeoPocketBaseAuthError, TokeoPocketBaseError, TokeoPocketBaseHandler
from tokeo.main import TokeoTest

# the url of the test apps; the fake server answers any url
URL = 'http://pocketbase.test:8090'

# a service identity known to the fake server
SERVICE = dict(auth_identity='service@example.com', auth_password='secret')

# the paths of the auth requests of both auth collections
USERS_LOGIN = ('POST', '/api/collections/users/auth-with-password')
USERS_REFRESH = ('POST', '/api/collections/users/auth-refresh')
SUPERUSERS_LOGIN = ('POST', '/api/collections/_superusers/auth-with-password')
SUPERUSERS_REFRESH = ('POST', '/api/collections/_superusers/auth-refresh')

# the paths of the auth and records api of PocketBase the fake answers
_ROUTE = re.compile(r'^/api/collections/([^/]+)/(auth-with-password|auth-refresh|records)(?:/([^/]+))?$')


class PocketbaseTest(TokeoTest):
    """A test app with the pocketbase extension and the given settings."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.pocketbase',
        ]

    def __init__(self, settings=None, **kw):
        config = init_defaults('pocketbase')
        config['pocketbase'].update(dict(url=URL), **(settings or {}))
        super().__init__(config_defaults=config, **kw)


def _jwt(exp, serial):
    # a token shaped like the one of PocketBase; exp None leaves the claim out
    def encode(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')

    payload = dict(type='auth', serial=serial)
    if exp is not None:
        payload['exp'] = exp
    return f'{encode(dict(alg="HS256", typ="JWT"))}.{encode(payload)}.signature'


def _error(status, message):
    # an error answer in the format of PocketBase
    return httpx.Response(status, json=dict(status=status, message=message, data={}))


class _FakePocketBase:
    """Answers the requests of the pocketbase client like a PocketBase server."""

    def __init__(self):
        # every request in the order it came in
        self.requests = []
        # the httpx kwargs tokeo handed to the pocketbase client
        self.client_kwargs = None
        # (collection, id): fields of the stored records
        self.records = {}
        # (collection, identity): password of the accounts that may log in
        self.accounts = {}
        # seconds a new token is valid, None issues tokens without exp
        self.lifetime = 3600
        # token: collection of every token issued and not revoked
        self.tokens = {}
        # (method, path): (status, message) answered instead of the route
        self.errors = {}
        # seconds a login takes, to widen the window for concurrent logins
        self.login_delay = 0
        self._serial = itertools.count(1)
        self._lock = threading.Lock()

    @property
    def calls(self):
        # the method and path of every request in the order they came in
        return [(request.method, request.url.path) for request in self.requests]

    def handle(self, request):
        with self._lock:
            self.requests.append(request)
        if (request.method, request.url.path) in self.errors:
            return _error(*self.errors[(request.method, request.url.path)])
        match = _ROUTE.match(request.url.path)
        if not match:
            return _error(404, 'Not found.')
        collection, action, id_ = match.groups()
        if action == 'auth-with-password':
            return self._login(collection, json.loads(request.content))
        if action == 'auth-refresh':
            return self._refresh(collection, request.headers.get('Authorization', ''))
        return self._records(request, collection, id_)

    def _issue(self, collection, identity):
        # a new token for the identity with its auth record
        with self._lock:
            serial = next(self._serial)
        exp = None if self.lifetime is None else int(time.time()) + self.lifetime
        token = _jwt(exp, serial)
        self.tokens[token] = collection
        record = dict(id=f'id_{identity}', collectionName=collection, email=identity)
        return httpx.Response(200, json=dict(token=token, record=record))

    def _login(self, collection, body):
        time.sleep(self.login_delay)
        identity, password = body.get('identity'), body.get('password')
        if self.accounts.get((collection, identity)) != password or password is None:
            return _error(400, 'Failed to authenticate.')
        return self._issue(collection, identity)

    def _refresh(self, collection, token):
        if self.tokens.get(token) != collection:
            return _error(401, 'The request requires valid record authorization token.')
        return self._issue(collection, 'refreshed')

    def _records(self, request, collection, id_):
        method = request.method
        if method == 'GET' and id_ is None:
            items = [dict(id=key[1], **fields) for key, fields in self.records.items() if key[0] == collection]
            page, per_page = int(request.url.params['page']), int(request.url.params['perPage'])
            start, end = (page - 1) * per_page, page * per_page
            return httpx.Response(
                200,
                json=dict(
                    page=page,
                    perPage=per_page,
                    totalItems=len(items),
                    totalPages=-(-len(items) // per_page),
                    items=items[start:end],
                ),
            )
        if method == 'POST':
            id_ = f'new{len(self.records) + 1}'
            self.records[(collection, id_)] = json.loads(request.content)
            return httpx.Response(200, json=dict(id=id_, collectionName=collection, **self.records[(collection, id_)]))
        if (collection, id_) not in self.records:
            return _error(404, "The requested resource wasn't found.")
        if method == 'GET':
            return httpx.Response(200, json=dict(id=id_, collectionName=collection, **self.records[(collection, id_)]))
        if method == 'PATCH':
            self.records[(collection, id_)].update(json.loads(request.content))
            return httpx.Response(200, json=dict(id=id_, collectionName=collection, **self.records[(collection, id_)]))
        del self.records[(collection, id_)]
        return httpx.Response(204)


@pytest.fixture
def server(monkeypatch):
    """A fake PocketBase server behind every pocketbase client of the test."""
    fake = _FakePocketBase()
    fake.records[('posts', 'abc')] = dict(title='Hello', views=3)
    fake.records[('posts', 'def')] = dict(title='World', views=5)
    fake.accounts[('users', SERVICE['auth_identity'])] = SERVICE['auth_password']
    connect = pocketbase.PocketBase

    def connect_to_fake(url, **kw):
        # keep the httpx kwargs of tokeo and route the client to the fake
        fake.client_kwargs = kw
        return connect(url, transport=httpx.MockTransport(fake.handle), **kw)

    monkeypatch.setattr(pocketbase, 'PocketBase', connect_to_fake)
    return fake


def _params(request):
    # the query parameters of a request as dict
    return dict(request.url.params)


def _body(request):
    # the json body of a request
    return json.loads(request.content)


# load and setup


def test_load_extends_the_app_with_the_handler_as_db(server):
    """After setup app.db is the handler and the client points at the url."""
    with PocketbaseTest() as app:
        assert isinstance(app.db, TokeoPocketBaseHandler)
        assert app._meta.db_handler == 'tokeo.pocketbase'
        assert app.db.pb.base_url == URL


def test_setup_fills_the_missing_settings_with_the_defaults(server):
    """Settings not given take the defaults, given ones stay."""
    with PocketbaseTest(dict(auth_token_min_valid_seconds=5)) as app:
        assert app.config.get_dict()['pocketbase'] == dict(
            url=URL,
            auth_identity=None,
            auth_password=None,
            auth_token_min_valid_seconds=5,
            concurrent_connections=None,
            tls_verify_cert=True,
            tls_verify_hostname=True,
            tls_ca=None,
        )


def test_setup_sends_no_request(server):
    """The setup sends no request, not even a login with a service identity."""
    with PocketbaseTest(SERVICE):
        pass
    assert server.requests == []


def test_setup_without_concurrent_connections_sets_no_limits(server):
    """Without concurrent_connections the client gets no limits, no context."""
    with PocketbaseTest():
        assert server.client_kwargs == {}


def test_setup_concurrent_connections_limit_the_connections(server):
    """concurrent_connections 5 hands httpx limits with max_connections 5."""
    with PocketbaseTest(dict(concurrent_connections=5)):
        assert server.client_kwargs['limits'].max_connections == 5


def test_setup_http_url_ignores_the_tls_settings(server):
    """A http url builds no tls context, even with verification switched off."""
    with PocketbaseTest(dict(tls_verify_cert=False, tls_verify_hostname=False)):
        assert 'verify' not in server.client_kwargs


def test_setup_https_url_with_secure_defaults_keeps_the_httpx_default(server):
    """A https url with the default tls settings hands no context of its own."""
    with PocketbaseTest(dict(url='https://pocketbase.test')):
        assert 'verify' not in server.client_kwargs


@pytest.mark.parametrize(
    'settings, verify_mode, check_hostname',
    [
        (dict(tls_verify_cert=False), ssl.CERT_NONE, False),
        (dict(tls_verify_hostname=False), ssl.CERT_REQUIRED, False),
        (dict(tls_ca=certifi.where()), ssl.CERT_REQUIRED, True),
    ],
    ids=['no_cert_check', 'no_hostname_check', 'own_ca'],
)
def test_setup_https_url_hands_the_tls_settings_as_context(server, settings, verify_mode, check_hostname):
    """A https url turns the tls settings into the ssl context of httpx."""
    with PocketbaseTest(dict(url='https://pocketbase.test', **settings)):
        context = server.client_kwargs['verify']
        assert isinstance(context, ssl.SSLContext)
        assert (context.verify_mode, context.check_hostname) == (verify_mode, check_hostname)


def test_setup_https_scheme_in_capitals_enables_tls(server):
    """HTTPS:// in capitals counts as https and gets the tls context."""
    with PocketbaseTest(dict(url='HTTPS://pocketbase.test', tls_verify_cert=False)):
        assert server.client_kwargs['verify'].verify_mode == ssl.CERT_NONE


def test_setup_missing_tls_ca_fails(server, tmp_path):
    """A tls_ca that does not exist fails the setup with FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        with PocketbaseTest(dict(url='https://pocketbase.test', tls_ca=str(tmp_path / 'missing.pem'))):
            pass


# collection


def test_collection_gives_the_record_service_of_the_sdk(server):
    """collection('posts') is the RecordService of the sdk, the same each time."""
    with PocketbaseTest() as app:
        posts = app.db.collection('posts')
        assert isinstance(posts, RecordService)
        assert posts.collection_id_or_name == 'posts'
        assert app.db.collection('posts') is posts


# get_one


def test_get_one_fetches_the_record_by_id(server):
    """get_one sends GET .../posts/records/abc and returns the record."""
    with PocketbaseTest() as app:
        record = app.db.get_one('posts', 'abc')
    assert server.calls == [('GET', '/api/collections/posts/records/abc')]
    assert _params(server.requests[0]) == {}
    assert isinstance(record, Record)
    assert (record.id, record.collection_name, record.title, record.views) == ('abc', 'posts', 'Hello', 3)


def test_get_one_anonymous_sends_no_authorization(server):
    """Without a service identity the request carries no Authorization header."""
    with PocketbaseTest() as app:
        app.db.get_one('posts', 'abc')
    assert 'Authorization' not in server.requests[0].headers


def test_get_one_without_cache_sends_no_cache(server):
    """cache=False adds cache=no-cache to the query."""
    with PocketbaseTest() as app:
        app.db.get_one('posts', 'abc', cache=False)
    assert _params(server.requests[0]) == dict(cache='no-cache')


@pytest.mark.parametrize('sort, params', [('-created', dict(sort='-created')), ('', {}), (None, {})], ids=['set', 'empty', 'none'])
def test_get_one_sends_sort_only_when_set(server, sort, params):
    """A sort is passed on as query, an empty or no sort is left out."""
    with PocketbaseTest() as app:
        app.db.get_one('posts', 'abc', sort=sort)
    assert _params(server.requests[0]) == params


def test_get_one_adds_q_to_the_query(server):
    """q expand and fields arrive as query next to cache and sort."""
    with PocketbaseTest() as app:
        app.db.get_one('posts', 'abc', sort='title', cache=False, q=dict(expand='author', fields='id,title'))
    assert _params(server.requests[0]) == dict(cache='no-cache', sort='title', expand='author', fields='id,title')


def test_get_one_missing_record_raises_the_sdk_error(server):
    """A missing record raises the ClientResponseError of the sdk, status 404."""
    with PocketbaseTest() as app:
        with pytest.raises(ClientResponseError) as caught:
            app.db.get_one('posts', 'missing')
    assert caught.value.status == 404
    assert caught.value.data['message'] == "The requested resource wasn't found."
    assert not isinstance(caught.value, TokeoPocketBaseError)


# get_list


def test_get_list_sends_the_default_query(server):
    """By default get_list asks for page 1 of 20 items with an empty filter."""
    with PocketbaseTest() as app:
        app.db.get_list('posts')
    assert server.calls == [('GET', '/api/collections/posts/records')]
    assert _params(server.requests[0]) == dict(filter='', page='1', perPage='20')


def test_get_list_sends_all_options(server):
    """page, perPage, filter, sort, cache=False and q all arrive as query."""
    with PocketbaseTest() as app:
        app.db.get_list('posts', page=2, perPage=50, filter='views > 3', sort='-created,title', cache=False, q=dict(expand='author'))
    assert _params(server.requests[0]) == dict(
        filter='views > 3',
        cache='no-cache',
        sort='-created,title',
        expand='author',
        page='2',
        perPage='50',
    )


@pytest.mark.parametrize('sort', ['', None], ids=['empty', 'none'])
def test_get_list_leaves_out_an_empty_sort(server, sort):
    """An empty or no sort sends no sort parameter."""
    with PocketbaseTest() as app:
        app.db.get_list('posts', sort=sort)
    assert 'sort' not in _params(server.requests[0])


def test_get_list_returns_the_page_as_list_result(server):
    """The answer comes back as ListResult with the records and the page counts."""
    with PocketbaseTest() as app:
        result = app.db.get_list('posts', perPage=1)
    assert isinstance(result, ListResult)
    assert (result.page, result.per_page, result.total_items, result.total_pages) == (1, 1, 2, 2)
    assert [(item.id, item.title) for item in result.items] == [('abc', 'Hello')]


def test_get_list_keeps_q_of_the_caller(server):
    """The q dict of the caller is not changed by the query built from it."""
    q = dict(expand='author')
    with PocketbaseTest() as app:
        app.db.get_list('posts', page=3, sort='title', q=q)
    assert q == dict(expand='author')


# create


def test_create_posts_the_fields_and_returns_the_record(server):
    """create sends POST .../posts/records with the fields as json body."""
    with PocketbaseTest() as app:
        record = app.db.create('posts', dict(title='New', views=0), q=dict(expand='author'))
    assert server.calls == [('POST', '/api/collections/posts/records')]
    assert _body(server.requests[0]) == dict(title='New', views=0)
    assert _params(server.requests[0]) == dict(expand='author')
    assert (record.id, record.title) == ('new3', 'New')


def test_create_without_fields_posts_an_empty_object(server):
    """create without fields sends {} as body and no query."""
    with PocketbaseTest() as app:
        app.db.create('posts')
    assert _body(server.requests[0]) == {}
    assert _params(server.requests[0]) == {}


def test_create_rejected_raises_the_sdk_error(server):
    """A 403 of the server raises ClientResponseError with its status and data."""
    server.errors[('POST', '/api/collections/posts/records')] = (403, 'Only superusers can perform this action.')
    with PocketbaseTest() as app:
        with pytest.raises(ClientResponseError) as caught:
            app.db.create('posts', dict(title='New'))
    assert caught.value.status == 403
    assert caught.value.data['message'] == 'Only superusers can perform this action.'


# update


def test_update_patches_the_fields_and_returns_the_record(server):
    """update sends PATCH .../posts/records/abc and returns the new record."""
    with PocketbaseTest() as app:
        record = app.db.update('posts', 'abc', dict(views=4), q=dict(fields='id,views'))
    assert server.calls == [('PATCH', '/api/collections/posts/records/abc')]
    assert _body(server.requests[0]) == dict(views=4)
    assert _params(server.requests[0]) == dict(fields='id,views')
    assert (record.title, record.views) == ('Hello', 4)


def test_update_without_fields_patches_an_empty_object(server):
    """update without fields sends {} as body."""
    with PocketbaseTest() as app:
        app.db.update('posts', 'abc')
    assert _body(server.requests[0]) == {}


# delete


def test_delete_removes_the_record_and_returns_true(server):
    """delete sends DELETE .../posts/records/abc with q and returns True."""
    with PocketbaseTest() as app:
        assert app.db.delete('posts', 'abc', q=dict(token='x')) is True
    assert server.calls == [('DELETE', '/api/collections/posts/records/abc')]
    assert _params(server.requests[0]) == dict(token='x')
    assert ('posts', 'abc') not in server.records


def test_delete_missing_record_raises_the_sdk_error(server):
    """Deleting a missing record raises ClientResponseError with status 404."""
    with PocketbaseTest() as app:
        with pytest.raises(ClientResponseError) as caught:
            app.db.delete('posts', 'missing')
    assert caught.value.status == 404


def test_delete_unreachable_server_raises_the_sdk_error(server, monkeypatch):
    """A connection error raises ClientResponseError with the httpx error."""

    def refuse(request):
        raise httpx.ConnectError('connection refused', request=request)

    monkeypatch.setattr(server, 'handle', refuse)
    with PocketbaseTest() as app:
        with pytest.raises(ClientResponseError) as caught:
            app.db.delete('posts', 'abc')
    assert isinstance(caught.value.original_error, httpx.ConnectError)


# ensure_auth


def _crud(db, method):
    # run one CRUD method of the handler on the post abc
    return dict(
        get_one=lambda: db.get_one('posts', 'abc'),
        get_list=lambda: db.get_list('posts'),
        create=lambda: db.create('posts', dict(title='New')),
        update=lambda: db.update('posts', 'abc', dict(views=4)),
        delete=lambda: db.delete('posts', 'abc'),
    )[method]()


@pytest.mark.parametrize('method', ['get_one', 'get_list', 'create', 'update', 'delete'])
def test_ensure_auth_logs_in_before_each_crud_method(server, method):
    """The first CRUD call logs the service in and runs with its token."""
    with PocketbaseTest(SERVICE) as app:
        _crud(app.db, method)
        token = app.db.pb.auth_store.token
    assert server.calls[0] == USERS_LOGIN
    assert server.requests[1].headers['Authorization'] == token


def test_ensure_auth_without_identity_stays_anonymous(server):
    """A password without auth_identity sends no login and no token."""
    with PocketbaseTest(dict(auth_password='secret')) as app:
        app.db.get_one('posts', 'abc')
    assert server.calls == [('GET', '/api/collections/posts/records/abc')]
    assert 'Authorization' not in server.requests[0].headers


def test_ensure_auth_sends_identity_and_password(server):
    """The login posts identity and password and no Authorization of its own."""
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
    assert _body(server.requests[0]) == dict(identity='service@example.com', password='secret')
    assert server.requests[0].headers.get('Authorization', '') == ''


def test_ensure_auth_logs_in_once_while_the_token_is_valid(server):
    """Three calls with a token valid for an hour log in once and never refresh."""
    with PocketbaseTest(SERVICE) as app:
        for _ in range(3):
            app.db.get_one('posts', 'abc')
    assert server.calls.count(USERS_LOGIN) == 1
    assert USERS_REFRESH not in server.calls


def test_ensure_auth_falls_back_to_superusers(server):
    """An identity unknown in users logs in on _superusers, which is remembered."""
    server.accounts = {('_superusers', 'admin@example.com'): 'secret'}
    with PocketbaseTest(dict(auth_identity='admin@example.com', auth_password='secret')) as app:
        app.db.get_one('posts', 'abc')
        assert app.db._auth_collection == '_superusers'
    assert server.calls[:2] == [USERS_LOGIN, SUPERUSERS_LOGIN]


def test_ensure_auth_wrong_password_raises_auth_error(server):
    """Credentials no collection accepts raise TokeoPocketBaseAuthError."""
    with PocketbaseTest(dict(SERVICE, auth_password='wrong')) as app:
        with pytest.raises(TokeoPocketBaseAuthError, match="identity 'service@example.com'") as caught:
            app.db.get_one('posts', 'abc')
    assert server.calls == [USERS_LOGIN, SUPERUSERS_LOGIN]
    assert isinstance(caught.value, TokeoError)
    assert caught.value.__cause__.status == 400


def test_ensure_auth_identity_without_password_fails_on_first_request(server):
    """An identity without password passes the setup and fails the first request."""
    with PocketbaseTest(dict(auth_identity='service@example.com')) as app:
        with pytest.raises(TokeoPocketBaseAuthError):
            app.db.get_one('posts', 'abc')
    assert _body(server.requests[0]) == dict(identity='service@example.com', password=None)


@pytest.mark.parametrize('min_valid, refreshes', [(60, 0), (300, 1)], ids=['enough_left', 'too_little_left'])
def test_ensure_auth_refreshes_below_the_minimum_validity(server, min_valid, refreshes):
    """A token valid 120 s is refreshed only below a minimum validity of 300 s."""
    server.lifetime = 120
    with PocketbaseTest(dict(SERVICE, auth_token_min_valid_seconds=min_valid)) as app:
        app.db.get_one('posts', 'abc')
        # the refreshed token is valid long enough for the further calls
        server.lifetime = 3600
        app.db.get_one('posts', 'abc')
        app.db.get_one('posts', 'abc')
    assert server.calls.count(USERS_LOGIN) == 1
    assert server.calls.count(USERS_REFRESH) == refreshes


def test_ensure_auth_refresh_replaces_the_token(server):
    """The refresh sends the old token, the next request carries the new one."""
    server.lifetime = 30
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        old = app.db.pb.auth_store.token
        app.db.get_one('posts', 'abc')
        new = app.db.pb.auth_store.token
    get = ('GET', '/api/collections/posts/records/abc')
    assert server.calls == [USERS_LOGIN, get, USERS_REFRESH, get]
    assert server.requests[2].headers['Authorization'] == old
    assert server.requests[3].headers['Authorization'] == new != old


def test_ensure_auth_rejected_refresh_logs_in_on_the_remembered_collection(server):
    """A revoked token fails the refresh, the new login runs on _superusers only."""
    server.accounts = {('_superusers', 'admin@example.com'): 'secret'}
    server.lifetime = 30
    with PocketbaseTest(dict(auth_identity='admin@example.com', auth_password='secret')) as app:
        app.db.get_one('posts', 'abc')
        server.tokens.clear()
        del server.requests[:]
        app.db.get_one('posts', 'abc')
    assert server.calls == [SUPERUSERS_REFRESH, SUPERUSERS_LOGIN, ('GET', '/api/collections/posts/records/abc')]


def test_ensure_auth_failed_relogin_raises_auth_error(server):
    """A failed refresh and new login raise, _superusers is not tried."""
    server.lifetime = 30
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        server.tokens.clear()
        server.accounts.clear()
        del server.requests[:]
        with pytest.raises(TokeoPocketBaseAuthError):
            app.db.get_one('posts', 'abc')
    assert server.calls == [USERS_REFRESH, USERS_LOGIN]


def test_ensure_auth_expired_token_logs_in_without_refresh(server):
    """An already expired token is not refreshed but replaced by a new login."""
    server.lifetime = -10
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        app.db.get_one('posts', 'abc')
    assert server.calls.count(USERS_LOGIN) == 2
    assert USERS_REFRESH not in server.calls


@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason='ensure_auth asks auth_store.is_valid, which raises KeyError on a token without exp (pocketbase.py:316)',
)
def test_ensure_auth_token_without_exp_logs_in_again(server):
    """A token without exp counts as unusable and leads to a new login."""
    server.lifetime = None
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        app.db.get_one('posts', 'abc')
    assert server.calls.count(USERS_LOGIN) == 2


def test_ensure_auth_concurrent_calls_log_in_once(server):
    """8 threads starting together on a fresh handler share one login."""
    server.login_delay = 0.05
    barrier = threading.Barrier(8)
    with PocketbaseTest(SERVICE) as app:

        def call():
            barrier.wait()
            return app.db.get_one('posts', 'abc').title

        with ThreadPoolExecutor(max_workers=8) as pool:
            titles = list(pool.map(lambda _: call(), range(8)))
    assert titles == ['Hello'] * 8
    assert server.calls.count(USERS_LOGIN) == 1


# close


def test_close_clears_the_service_login(server):
    """close drops the token, the next call logs in again."""
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        app.db.close()
        assert (app.db.pb.auth_store.token, app.db._auth_collection) == ('', None)
        app.db.get_one('posts', 'abc')
    assert server.calls.count(USERS_LOGIN) == 2


def test_close_of_the_app_clears_the_token(server):
    """Closing the app runs close of the handler, no token is left."""
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        db = app.db
        assert db.pb.auth_store.token
    assert db.pb.auth_store.token == ''


def test_close_anonymous_sends_nothing(server):
    """close of an anonymous handler sends no request and raises nothing."""
    with PocketbaseTest() as app:
        app.db.close()
    assert server.requests == []
