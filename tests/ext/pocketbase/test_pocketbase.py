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
import types
from concurrent.futures import ThreadPoolExecutor

import certifi
import httpx
import pytest
from cement.utils.misc import init_defaults
from tokeo.core.exc import TokeoError
from tokeo.core.pocketbase import client as pocketbase_client
from tokeo.core.pocketbase import TokeoPocketBaseCollection, TokeoPocketBaseListResult, TokeoPocketBaseClient, TokeoPocketBaseRecord
from tokeo.ext import pocketbase as pocketbase_ext
from tokeo.ext.pocketbase import TokeoPocketBaseAuthError, TokeoPocketBaseError, TokeoPocketBase
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
_ROUTE = re.compile(r'^/api/collections/([^/]+)/(auth-with-password|auth-refresh|impersonate|records)(?:/([^/]+))?$')


class PocketbaseTest(TokeoTest):
    """A test app with the pocketbase extension and the given pocketbase config."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.pocketbase',
        ]

    def __init__(self, pocketbase_config=None, **kw):
        config = init_defaults('pocketbase')
        config['pocketbase'].update(dict(url=URL), **(pocketbase_config or {}))
        super().__init__(config_defaults=config, **kw)


def _make_jwt(exp, serial):
    # a token shaped like the one of PocketBase; exp None leaves the claim out
    def encode(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')

    payload = dict(type='auth', serial=serial)
    if exp is not None:
        payload['exp'] = exp
    return f'{encode(dict(alg="HS256", typ="JWT"))}.{encode(payload)}.signature'


def _make_error(status, message):
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
            return _make_error(*self.errors[(request.method, request.url.path)])
        match = _ROUTE.match(request.url.path)
        if not match:
            return _make_error(404, 'Not found.')
        collection, action, id_ = match.groups()
        if action == 'auth-with-password':
            return self._login(collection, json.loads(request.content))
        if action == 'auth-refresh':
            return self._refresh(collection, request.headers.get('Authorization', ''))
        if action == 'impersonate':
            if self.tokens.get(request.headers.get('Authorization', '')) != '_superusers':
                return _make_error(403, 'Only superusers can perform this action.')
            return self._issue(collection, id_)
        return self._handle_records(request, collection, id_)

    def _issue(self, collection, identity):
        # a new token for the identity with its auth record
        with self._lock:
            serial = next(self._serial)
        exp = None if self.lifetime is None else int(time.time()) + self.lifetime
        token = _make_jwt(exp, serial)
        self.tokens[token] = collection
        record = dict(id=f'id_{identity}', collectionName=collection, email=identity)
        return httpx.Response(200, json=dict(token=token, record=record))

    def _login(self, collection, body):
        time.sleep(self.login_delay)
        identity, password = body.get('identity'), body.get('password')
        if self.accounts.get((collection, identity)) != password or password is None:
            return _make_error(400, 'Failed to authenticate.')
        return self._issue(collection, identity)

    def _refresh(self, collection, token):
        if self.tokens.get(token) != collection:
            return _make_error(401, 'The request requires valid record authorization token.')
        return self._issue(collection, 'refreshed')

    def _handle_records(self, request, collection, id_):
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
            return _make_error(404, "The requested resource wasn't found.")
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

    def connect_to_fake(url, **kw):
        # keep the httpx kwargs of tokeo and route the client to the fake
        fake.client_kwargs = {key: value for key, value in kw.items() if key != 'before_request'}
        return TokeoPocketBaseClient(url, transport=httpx.MockTransport(lambda request: fake.handle(request)), **kw)

    monkeypatch.setattr(pocketbase_ext, 'TokeoPocketBaseClient', connect_to_fake)
    return fake


def _get_params(request):
    # the query parameters of a request as dict
    return dict(request.url.params)


def _get_body(request):
    # the json body of a request
    return json.loads(request.content)


# load and setup


def test_load_extends_the_app_with_the_handler_as_db(server):
    """After setup app.db is the handler and the client points at the url."""
    with PocketbaseTest() as app:
        assert isinstance(app.db, TokeoPocketBase)
        assert app._meta.db_handler == 'tokeo.pocketbase'
        assert app.db.pb.url == URL


def test_setup_fills_the_missing_options_with_the_defaults(server):
    """Options not given take the defaults, given ones stay."""
    with PocketbaseTest(dict(auth_token_min_valid_seconds=5)) as app:
        assert app.config.get_dict()['pocketbase'] == dict(
            url=URL,
            auth_identity=None,
            auth_password=None,
            auth_token_min_valid_seconds=5,
            auth_check_seconds=60,
            realtime_read_timeout=330,
            realtime_max_pending=1000,
            realtime_auth_check_seconds=60,
            realtime_max_delay_seconds=30,
            max_connections=None,
            tls_verify_cert=True,
            tls_verify_hostname=True,
            tls_ca_path=None,
        )


def test_setup_gives_the_realtime_options_of_the_config(server):
    """The realtime_* config becomes the options of a channel, as numbers."""
    with PocketbaseTest(dict(realtime_read_timeout='90', realtime_max_pending='50')) as app:
        assert app.db.realtime_options == dict(read_timeout=90.0, max_pending=50, auth_check_seconds=60.0, max_delay_seconds=30.0)


@pytest.mark.parametrize('max_pending', [0, '0', 'many', None], ids=['zero', 'zero_str', 'text', 'none'])
def test_setup_refuses_a_realtime_max_pending_below_1(server, max_pending):
    """A realtime_max_pending that is no integer of 1 or more stops the setup."""
    expected = f'pocketbase.realtime_max_pending must be an integer of 1 or more, not {max_pending!r}'
    with pytest.raises(TokeoPocketBaseError, match=f'^{re.escape(expected)}$'):
        with PocketbaseTest(dict(realtime_max_pending=max_pending)):
            pass


@pytest.mark.parametrize(
    'key', ['auth_check_seconds', 'realtime_read_timeout', 'realtime_auth_check_seconds', 'realtime_max_delay_seconds']
)
@pytest.mark.parametrize('value', [0, '0', -1, 'soon', None, float('inf')], ids=['zero', 'zero_str', 'negative', 'text', 'none', 'inf'])
def test_setup_refuses_seconds_not_greater_than_0(server, key, value):
    """A time option no number greater than 0 stops the setup, naming it."""
    expected = f'pocketbase.{key} must be a number of seconds greater than 0, not {value!r}'
    with pytest.raises(TokeoPocketBaseError, match=f'^{re.escape(expected)}$'):
        with PocketbaseTest({key: value}):
            pass


def test_setup_takes_seconds_as_text(server):
    """A time option as text like '5' is taken as a number."""
    with PocketbaseTest(dict(auth_check_seconds='5', realtime_auth_check_seconds='2.5', realtime_max_delay_seconds='10')) as app:
        assert app.db._auth_check == 5.0
        assert app.db.realtime_options['auth_check_seconds'] == 2.5
        assert app.db.realtime_options['max_delay_seconds'] == 10.0


def test_setup_sends_no_request(server):
    """The setup sends no request, not even a login with a service identity."""
    with PocketbaseTest(SERVICE):
        pass
    assert server.requests == []


def test_setup_without_max_connections_sets_no_limits(server):
    """Without max_connections the client gets no limits, no context."""
    with PocketbaseTest():
        assert server.client_kwargs == {}


def test_setup_max_connections_limit_the_connections(server):
    """max_connections 5 hands httpx limits with max_connections 5."""
    with PocketbaseTest(dict(max_connections=5)):
        assert server.client_kwargs['limits'].max_connections == 5


def test_setup_http_url_ignores_the_tls_options(server):
    """A http url builds no tls context, even with verification switched off."""
    with PocketbaseTest(dict(tls_verify_cert=False, tls_verify_hostname=False)):
        assert 'verify' not in server.client_kwargs


def test_setup_https_url_with_secure_defaults_keeps_the_httpx_default(server):
    """A https url with the default tls options hands no context of its own."""
    with PocketbaseTest(dict(url='https://pocketbase.test')):
        assert 'verify' not in server.client_kwargs


@pytest.mark.parametrize(
    'tls_options, verify_mode, check_hostname',
    [
        (dict(tls_verify_cert=False), ssl.CERT_NONE, False),
        (dict(tls_verify_hostname=False), ssl.CERT_REQUIRED, False),
        (dict(tls_ca_path=certifi.where()), ssl.CERT_REQUIRED, True),
    ],
    ids=['no_cert_check', 'no_hostname_check', 'own_ca'],
)
def test_setup_https_url_hands_the_tls_options_as_context(server, tls_options, verify_mode, check_hostname):
    """A https url turns the tls options into the ssl context of httpx."""
    with PocketbaseTest(dict(url='https://pocketbase.test', **tls_options)):
        context = server.client_kwargs['verify']
        assert isinstance(context, ssl.SSLContext)
        assert (context.verify_mode, context.check_hostname) == (verify_mode, check_hostname)


def test_setup_https_scheme_in_capitals_enables_tls(server):
    """HTTPS:// in capitals counts as https and gets the tls context."""
    with PocketbaseTest(dict(url='HTTPS://pocketbase.test', tls_verify_cert=False)):
        assert server.client_kwargs['verify'].verify_mode == ssl.CERT_NONE


def test_setup_missing_tls_ca_path_fails(server, tmp_path):
    """A tls_ca_path that does not exist fails the setup with FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        with PocketbaseTest(dict(url='https://pocketbase.test', tls_ca_path=str(tmp_path / 'missing.pem'))):
            pass


# collection


def test_collection_gives_the_record_service_of_the_driver(server):
    """collection('posts') is the same TokeoPocketBaseCollection each time."""
    with PocketbaseTest() as app:
        posts = app.db.collection('posts')
        assert isinstance(posts, TokeoPocketBaseCollection)
        assert posts.collection == 'posts'
        assert app.db.collection('posts') is posts


# get_one


def test_get_one_fetches_the_record_by_id(server):
    """get_one sends GET .../posts/records/abc and returns the record."""
    with PocketbaseTest() as app:
        record = app.db.get_one('posts', 'abc')
    assert server.calls == [('GET', '/api/collections/posts/records/abc')]
    assert _get_params(server.requests[0]) == {}
    assert isinstance(record, TokeoPocketBaseRecord)
    assert (record.id, record.collectionName, record.title, record.views) == ('abc', 'posts', 'Hello', 3)


def test_get_one_anonymous_sends_no_authorization(server):
    """Without a service identity the request carries no Authorization header."""
    with PocketbaseTest() as app:
        app.db.get_one('posts', 'abc')
    assert 'Authorization' not in server.requests[0].headers


def test_get_one_sends_expand_fields_and_query(server):
    """expand, fields and the keys of query arrive as query parameters."""
    with PocketbaseTest() as app:
        app.db.get_one('posts', 'abc', expand='author', fields='id,title', query=dict(x='y'))
    assert _get_params(server.requests[0]) == dict(expand='author', fields='id,title', x='y')


def test_get_one_query_wins_over_the_arguments(server):
    """A key of query that is also an argument is no error, the query value wins."""
    with PocketbaseTest() as app:
        app.db.get_one('posts', 'abc', fields='id', query=dict(fields='id,title'))
    assert _get_params(server.requests[0]) == dict(fields='id,title')


def test_get_one_missing_record_raises_an_error(server):
    """A missing record raises TokeoPocketBaseError with status 404."""
    with PocketbaseTest() as app:
        with pytest.raises(TokeoPocketBaseError) as err_info:
            app.db.get_one('posts', 'missing')
    assert err_info.value.status == 404
    assert "The requested resource wasn't found." in str(err_info.value)


# get_list


def test_get_list_sends_the_default_query(server):
    """By default get_list asks for page 1 of 30 items without a filter."""
    with PocketbaseTest() as app:
        app.db.get_list('posts')
    assert server.calls == [('GET', '/api/collections/posts/records')]
    assert _get_params(server.requests[0]) == dict(page='1', perPage='30')


def test_get_list_sends_all_options(server):
    """page, per_page, filter, sort, expand, fields, skip_total and query arrive."""
    with PocketbaseTest() as app:
        app.db.get_list('posts', 2, 50, 'views > 3', '-created,title', 'author', 'id,title', True, dict(x='y'))
    assert _get_params(server.requests[0]) == dict(
        filter='views > 3',
        sort='-created,title',
        expand='author',
        fields='id,title',
        skipTotal='true',
        x='y',
        page='2',
        perPage='50',
    )


@pytest.mark.parametrize('sort', ['', None], ids=['empty', 'none'])
def test_get_list_leaves_out_an_empty_sort(server, sort):
    """An empty or no sort sends no sort parameter."""
    with PocketbaseTest() as app:
        app.db.get_list('posts', sort=sort)
    assert 'sort' not in _get_params(server.requests[0])


def test_get_list_returns_the_page_as_list_result(server):
    """The answer comes back as TokeoPocketBaseListResult, records and counts."""
    with PocketbaseTest() as app:
        result = app.db.get_list('posts', per_page=1)
    assert isinstance(result, TokeoPocketBaseListResult)
    assert (result.page, result.per_page, result.total_items, result.total_pages) == (1, 1, 2, 2)
    assert [(item.id, item.title) for item in result.items] == [('abc', 'Hello')]


def test_get_list_query_wins_over_the_arguments(server):
    """A filter or sort in query is no error, the query value wins."""
    with PocketbaseTest() as app:
        app.db.get_list('posts', filter='views > 0', sort='title', query=dict(filter='views > 3', sort='-created'))
    params = _get_params(server.requests[0])
    assert (params['filter'], params['sort']) == ('views > 3', '-created')


def test_get_list_keeps_query_of_the_caller(server):
    """The query dict of the caller stays unchanged."""
    query = dict(expand='author')
    with PocketbaseTest() as app:
        app.db.get_list('posts', page=3, sort='title', query=query)
    assert query == dict(expand='author')


# get_full_list and get_first


def test_get_full_list_gives_all_records_page_by_page(server):
    """get_full_list with per_page 1 asks page by page and gives both posts."""
    with PocketbaseTest() as app:
        records = app.db.get_full_list('posts', per_page=1)
    assert [record.id for record in records] == ['abc', 'def']
    assert [_get_params(request)['page'] for request in server.requests] == ['1', '2', '3']


def test_get_first_gives_the_first_record(server):
    """get_first asks for one record without total and gives it."""
    with PocketbaseTest() as app:
        record = app.db.get_first('posts', "title = 'Hello'")
    assert record.id == 'abc'
    assert _get_params(server.requests[0]) == dict(page='1', perPage='1', filter="title = 'Hello'", skipTotal='true')


# create


def test_create_posts_the_fields_and_returns_the_record(server):
    """create sends POST .../posts/records with the fields as json body."""
    with PocketbaseTest() as app:
        record = app.db.create('posts', dict(title='New', views=0), expand='author')
    assert server.calls == [('POST', '/api/collections/posts/records')]
    assert _get_body(server.requests[0]) == dict(title='New', views=0)
    assert _get_params(server.requests[0]) == dict(expand='author')
    assert (record.id, record.title) == ('new3', 'New')


def test_create_without_fields_posts_an_empty_object(server):
    """create without fields sends {} as body and no query."""
    with PocketbaseTest() as app:
        app.db.create('posts')
    assert _get_body(server.requests[0]) == {}
    assert _get_params(server.requests[0]) == {}


def test_create_rejected_raises_an_error(server):
    """A 403 of the server raises TokeoPocketBaseError with status and message."""
    server.errors[('POST', '/api/collections/posts/records')] = (403, 'Only superusers can perform this action.')
    with PocketbaseTest() as app:
        with pytest.raises(TokeoPocketBaseError) as err_info:
            app.db.create('posts', dict(title='New'))
    assert err_info.value.status == 403
    assert 'Only superusers can perform this action.' in str(err_info.value)


# update


def test_update_patches_the_fields_and_returns_the_record(server):
    """update sends PATCH .../posts/records/abc and returns the new record."""
    with PocketbaseTest() as app:
        record = app.db.update('posts', 'abc', dict(views=4), fields='id,views')
    assert server.calls == [('PATCH', '/api/collections/posts/records/abc')]
    assert _get_body(server.requests[0]) == dict(views=4)
    assert _get_params(server.requests[0]) == dict(fields='id,views')
    assert (record.title, record.views) == ('Hello', 4)


def test_update_without_fields_patches_an_empty_object(server):
    """update without fields sends {} as body."""
    with PocketbaseTest() as app:
        app.db.update('posts', 'abc')
    assert _get_body(server.requests[0]) == {}


# delete


def test_delete_removes_the_record_and_returns_true(server):
    """delete sends DELETE .../posts/records/abc with query and returns True."""
    with PocketbaseTest() as app:
        assert app.db.delete('posts', 'abc', query=dict(token='x')) is True
    assert server.calls == [('DELETE', '/api/collections/posts/records/abc')]
    assert _get_params(server.requests[0]) == dict(token='x')
    assert ('posts', 'abc') not in server.records


def test_delete_missing_record_raises_an_error(server):
    """Deleting a missing record raises TokeoPocketBaseError with status 404."""
    with PocketbaseTest() as app:
        with pytest.raises(TokeoPocketBaseError) as err_info:
            app.db.delete('posts', 'missing')
    assert err_info.value.status == 404


def test_delete_unreachable_server_raises_an_error(server, monkeypatch):
    """A connection error raises TokeoPocketBaseError with the httpx error."""

    def refuse(request):
        raise httpx.ConnectError('connection refused', request=request)

    monkeypatch.setattr(server, 'handle', refuse)
    with PocketbaseTest() as app:
        with pytest.raises(TokeoPocketBaseError) as err_info:
            app.db.delete('posts', 'abc')
    assert err_info.value.status == 0 and isinstance(err_info.value.__cause__, httpx.ConnectError)


# keep_auth


def _run_crud(db, method):
    # run one CRUD method of the handler on the post abc
    return dict(
        get_one=lambda: db.get_one('posts', 'abc'),
        get_list=lambda: db.get_list('posts'),
        create=lambda: db.create('posts', dict(title='New')),
        update=lambda: db.update('posts', 'abc', dict(views=4)),
        delete=lambda: db.delete('posts', 'abc'),
    )[method]()


@pytest.mark.parametrize('method', ['get_one', 'get_list', 'create', 'update', 'delete'])
def test_keep_auth_logs_in_before_each_crud_method(server, method):
    """The first CRUD call logs the service in and runs with its token."""
    with PocketbaseTest(SERVICE) as app:
        _run_crud(app.db, method)
        token = app.db.pb.token
    assert server.calls[0] == USERS_LOGIN
    assert server.requests[1].headers['Authorization'] == token


def test_keep_auth_runs_before_the_records_of_collection(server):
    """collection() logs the service in as well, by the hook of the client."""
    with PocketbaseTest(SERVICE) as app:
        app.db.collection('posts').get_list()
        token = app.db.pb.token
    assert server.calls[0] == USERS_LOGIN
    assert server.requests[1].headers['Authorization'] == token


def test_keep_auth_without_identity_stays_anonymous(server):
    """A password without auth_identity sends no login and no token."""
    with PocketbaseTest(dict(auth_password='secret')) as app:
        app.db.get_one('posts', 'abc')
    assert server.calls == [('GET', '/api/collections/posts/records/abc')]
    assert 'Authorization' not in server.requests[0].headers


def test_keep_auth_sends_identity_and_password(server):
    """The login posts identity and password and no Authorization of its own."""
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
    assert _get_body(server.requests[0]) == dict(identity='service@example.com', password='secret')
    assert server.requests[0].headers.get('Authorization', '') == ''


def test_keep_auth_logs_in_once_while_the_token_is_valid(server):
    """Three calls with a token valid for an hour log in once and never refresh."""
    with PocketbaseTest(SERVICE) as app:
        for _ in range(3):
            app.db.get_one('posts', 'abc')
    assert server.calls.count(USERS_LOGIN) == 1
    assert USERS_REFRESH not in server.calls


def test_keep_auth_falls_back_to_superusers(server):
    """An identity unknown in users logs in on _superusers, which is remembered."""
    server.accounts = {('_superusers', 'admin@example.com'): 'secret'}
    with PocketbaseTest(dict(auth_identity='admin@example.com', auth_password='secret')) as app:
        app.db.get_one('posts', 'abc')
        assert app.db._auth_collection == '_superusers'
    assert server.calls[:2] == [USERS_LOGIN, SUPERUSERS_LOGIN]


def test_keep_auth_wrong_password_raises_auth_error(server):
    """Credentials no collection accepts raise TokeoPocketBaseAuthError."""
    with PocketbaseTest(dict(SERVICE, auth_password='wrong')) as app:
        with pytest.raises(TokeoPocketBaseAuthError, match="identity 'service@example.com'") as err_info:
            app.db.get_one('posts', 'abc')
    assert server.calls == [USERS_LOGIN, SUPERUSERS_LOGIN]
    assert isinstance(err_info.value, TokeoError)
    assert err_info.value.__cause__.status == 400


def test_keep_auth_server_error_is_no_wrong_password(server):
    """A login failing by the server raises its error, tries no other collection."""
    server.errors[USERS_LOGIN] = (503, 'Service unavailable.')
    with PocketbaseTest(SERVICE) as app:
        with pytest.raises(TokeoPocketBaseError) as err_info:
            app.db.get_one('posts', 'abc')
    assert not isinstance(err_info.value, TokeoPocketBaseAuthError) and err_info.value.status == 503
    assert server.calls == [USERS_LOGIN]


def test_keep_auth_identity_without_password_fails_on_first_request(server):
    """An identity without password passes the setup and fails the first request."""
    with PocketbaseTest(dict(auth_identity='service@example.com')) as app:
        with pytest.raises(TokeoPocketBaseAuthError):
            app.db.get_one('posts', 'abc')
    assert _get_body(server.requests[0]) == dict(identity='service@example.com', password=None)


@pytest.mark.parametrize('min_valid, refreshes', [(60, 0), (300, 1)], ids=['enough_left', 'too_little_left'])
def test_keep_auth_refreshes_below_the_minimum_validity(server, min_valid, refreshes):
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


def test_keep_auth_refresh_replaces_the_token(server):
    """The refresh sends the old token, the next request carries the new one."""
    server.lifetime = 30
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        old = app.db.pb.token
        app.db.get_one('posts', 'abc')
        new = app.db.pb.token
    get = ('GET', '/api/collections/posts/records/abc')
    assert server.calls == [USERS_LOGIN, get, USERS_REFRESH, get]
    assert server.requests[2].headers['Authorization'] == old
    assert server.requests[3].headers['Authorization'] == new != old


def test_keep_auth_rejected_refresh_logs_in_on_the_remembered_collection(server):
    """A revoked token fails the refresh, the new login runs on _superusers only."""
    server.accounts = {('_superusers', 'admin@example.com'): 'secret'}
    server.lifetime = 30
    with PocketbaseTest(dict(auth_identity='admin@example.com', auth_password='secret')) as app:
        app.db.get_one('posts', 'abc')
        server.tokens.clear()
        del server.requests[:]
        app.db.get_one('posts', 'abc')
    assert server.calls == [SUPERUSERS_REFRESH, SUPERUSERS_LOGIN, ('GET', '/api/collections/posts/records/abc')]


def test_keep_auth_failed_relogin_raises_auth_error(server):
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


def test_keep_auth_expired_token_logs_in_without_refresh(server):
    """An already expired token is not refreshed but replaced by a new login."""
    server.lifetime = -10
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        app.db.get_one('posts', 'abc')
    assert server.calls.count(USERS_LOGIN) == 2
    assert USERS_REFRESH not in server.calls


def test_keep_auth_token_without_exp_logs_in_again(server):
    """A token without exp counts as unusable and leads to a new login."""
    server.lifetime = None
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        app.db.get_one('posts', 'abc')
    assert server.calls.count(USERS_LOGIN) == 2


def test_keep_auth_concurrent_calls_log_in_once(server):
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


@pytest.fixture
def clock(monkeypatch):
    """The clock of the AuthKeeper, auth_check_seconds later at each look."""
    looks = itertools.count(0, TokeoPocketBase.Meta.config_defaults['auth_check_seconds'])
    # only the client module looks at it, httpx keeps the real time
    monkeypatch.setattr(pocketbase_client, 'time', types.SimpleNamespace(monotonic=lambda: next(looks)))


def test_keep_auth_confirms_the_login_on_the_server_after_auth_check_seconds(server, clock):
    """Once auth_check_seconds are over, the next request asks the server first."""
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        app.db.get_one('posts', 'abc')
    get = ('GET', '/api/collections/posts/records/abc')
    assert server.calls == [USERS_LOGIN, get, USERS_REFRESH, get]


def test_keep_auth_login_refused_on_the_server_logs_in_anew(server, clock):
    """A token revoked on the server, e.g. by a new password, logs in anew."""
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        server.tokens.clear()
        del server.requests[:]
        app.db.get_one('posts', 'abc')
        token = app.db.pb.token
    assert server.calls == [USERS_REFRESH, USERS_LOGIN, ('GET', '/api/collections/posts/records/abc')]
    assert server.requests[2].headers['Authorization'] == token


def test_keep_auth_changed_password_raises_instead_of_a_guest_request(server, clock):
    """A revoked token and a changed password raise, nothing goes as guest."""
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        server.tokens.clear()
        server.accounts.clear()
        del server.requests[:]
        with pytest.raises(TokeoPocketBaseAuthError):
            app.db.get_one('posts', 'abc')
    assert server.calls == [USERS_REFRESH, USERS_LOGIN]


# impersonate

# a service identity of the _superusers, which may impersonate
SUPERUSER = dict(auth_identity='admin@example.com', auth_password='secret')


def test_impersonate_gives_a_client_of_its_own_acting_as_the_record(server):
    """The client carries the token of the record, the service keeps its own."""
    server.accounts = {('_superusers', 'admin@example.com'): 'secret'}
    with PocketbaseTest(SUPERUSER) as app:
        client = app.db.impersonate('users', 'u1', duration=600)
        client.collection('posts').get_list()
        assert (client.auth_record.email, client.http_client is app.db.pb.http_client) == ('u1', False)
        assert server.requests[-1].headers['Authorization'] == client.token != app.db.pb.token
        assert _get_body(server.requests[-2]) == dict(duration=600)
        client.close()


def test_impersonate_renews_a_login_refused_on_the_server_by_a_new_impersonation(server, clock):
    """A revoked token of the record is impersonated anew for the request."""
    server.accounts = {('_superusers', 'admin@example.com'): 'secret'}
    with PocketbaseTest(SUPERUSER) as app:
        client = app.db.impersonate('users', 'u1')
        old = client.token
        del server.tokens[old]
        client.collection('posts').get_list()
        assert (client.login_cnt, server.requests[-1].headers['Authorization']) == (2, client.token)
        assert client.token != old
        client.close()
    assert ('POST', '/api/collections/users/impersonate/u1') in server.calls[-3:]


def test_impersonate_by_a_service_without_superuser_raises(server):
    """A service of users may not impersonate, it raises with status 403."""
    with PocketbaseTest(SERVICE) as app:
        with pytest.raises(TokeoPocketBaseAuthError) as err_info:
            app.db.impersonate('users', 'u1')
    assert err_info.value.status == 403


# nicegui


@pytest.mark.parametrize(
    'extensions',
    [['tokeo.ext.pocketbase', 'tokeo.ext.nicegui'], ['tokeo.ext.nicegui', 'tokeo.ext.pocketbase']],
    ids=['pocketbase_first', 'nicegui_first'],
)
def test_nicegui_gets_the_live_data_as_pb_whatever_the_order(server, extensions):
    """With nicegui, loaded before or after pocketbase, app.nicegui.pb is there."""
    from tokeo.ext.pocketbase.live import NiceguiPocketBase

    class BothTest(PocketbaseTest):

        class Meta:
            pass

    BothTest.Meta.extensions = ['tokeo.ext.print', *extensions]
    with BothTest() as app:
        assert isinstance(app.nicegui.pb, NiceguiPocketBase) and app.nicegui.pb.app is app


def test_without_nicegui_the_live_data_stays_out(server):
    """Without nicegui its hook is not defined, pocketbase sets up alone."""
    with PocketbaseTest() as app:
        assert not app.hook.defined('tokeo_nicegui_extend') and not hasattr(app, 'nicegui')


# close


def test_close_clears_the_service_login_and_closes_the_client(server):
    """close drops the token and closes the connection pool of the client."""
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        app.db.close()
        assert (app.db.pb.token, app.db._auth_collection) == (None, None)
        assert app.db.pb.http_client.is_closed


def test_close_of_the_app_clears_the_token(server):
    """Closing the app runs close of the handler, no token or connection is left."""
    with PocketbaseTest(SERVICE) as app:
        app.db.get_one('posts', 'abc')
        db = app.db
        assert db.pb.token
    assert db.pb.token is None and db.pb.http_client.is_closed


def test_close_anonymous_sends_nothing(server):
    """close of an anonymous handler sends no request and raises nothing."""
    with PocketbaseTest() as app:
        app.db.close()
    assert server.requests == []
