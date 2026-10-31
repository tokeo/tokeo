"""
Tests for tokeo.core.pocketbase without a server: filters with quoted
values, records with access by attribute, pages of records, the requests
the client and its record methods send (path, method, query, body), the
errors they raise, login, refresh, impersonation, batches and health.

Every client of a test talks to an httpx.MockTransport that answers like
PocketBase and records each request.
"""

import json
import base64
from datetime import datetime, timezone, timedelta

import httpx
import pytest
from tokeo.core.exc import TokeoError
from tokeo.core.pocketbase import (
    Batch,
    Collection,
    ListResult,
    PocketBase,
    Record,
    TokeoPocketBaseAuthError,
    TokeoPocketBaseError,
    build_filter,
)

URL = 'http://pocketbase.test:8090'


class _Server:
    """Answers each request by a function of the test and records it."""

    def __init__(self):
        # every request in the order it came in
        self.requests = []
        # (method, path): the answer, a dict as json or an httpx.Response
        self.answers = {}
        # a function of the test that answers instead
        self.respond = None

    def handle(self, request):
        self.requests.append(request)
        if self.respond:
            return self.respond(request)
        answer = self.answers.get((request.method, request.url.path), {})
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


@pytest.fixture
def server():
    """A fake server of the test."""
    return _Server()


@pytest.fixture
def pb(server):
    """A client that talks to the fake server."""
    with PocketBase(URL, transport=httpx.MockTransport(server.handle)) as client:
        yield client


def _params(request):
    # the query parameters of a request as dict
    return dict(request.url.params)


def _body(request):
    # the json body of a request
    return json.loads(request.content)


def _jwt(lifetime):
    # a token shaped like the one of PocketBase; None leaves exp out
    def encode(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')

    payload = {'type': 'auth'}
    if lifetime is not None:
        payload['exp'] = int(datetime.now(timezone.utc).timestamp()) + lifetime
    return f'{encode({"alg": "HS256", "typ": "JWT"})}.{encode(payload)}.signature'


def _auth(token, record_id='u1'):
    # the answer of a login
    return {'token': token, 'record': {'id': record_id, 'email': 'anna@tokeo.test'}}


# build_filter


@pytest.mark.parametrize(
    'value, literal',
    [
        ('plain', "'plain'"),
        ("it's", "'it\\'s'"),
        ('back\\', "'back\\\\'"),
        ("x\\' || id != '", "'x\\\\\\' || id != \\''"),
        ('q"uote', "'q\"uote'"),
        (True, 'true'),
        (False, 'false'),
        (None, 'null'),
        (3, '3'),
        (2.5, '2.5'),
    ],
    ids=['str', 'quote', 'backslash', 'breakout', 'double_quote', 'true', 'false', 'none', 'int', 'float'],
)
def test_build_filter_quotes_each_value_for_the_filter_syntax(value, literal):
    """Strings get \\ and ' escaped in single quotes, the others their literal."""
    assert build_filter('title = {:v}', {'v': value}) == f'title = {literal}'


@pytest.mark.parametrize(
    'value',
    [datetime(2026, 10, 31, 9, 5, 7, 123456, tzinfo=timezone(timedelta(hours=2))), datetime(2026, 10, 31, 7, 5, 7, 123456)],
    ids=['aware', 'naive'],
)
def test_build_filter_takes_a_datetime_as_utc(value):
    """A datetime becomes the utc format of PocketBase, a naive one is utc."""
    assert build_filter('created > {:d}', {'d': value}) == "created > '2026-10-31 07:05:07.123Z'"


def test_build_filter_replaces_each_placeholder_once():
    """A value that looks like a placeholder is not replaced again."""
    assert build_filter('a = {:a} && b = {:b}', {'a': '{:b}', 'b': 'x'}) == "a = '{:b}' && b = 'x'"


def test_build_filter_without_placeholders_keeps_the_expression():
    """A filter without placeholders and without params stays as it is."""
    assert build_filter('views > 3') == 'views > 3'


@pytest.mark.parametrize(
    'expr, params, message',
    [
        ('a = {:a}', {}, 'placeholder {:a} has no value'),
        ('a = {:a}', {'a': [1]}, 'type list is not supported'),
        ('a = {:a}', {'a': {'x': 1}}, 'type dict is not supported'),
    ],
    ids=['missing', 'list', 'dict'],
)
def test_build_filter_refuses_a_missing_or_unsupported_value(expr, params, message):
    """A placeholder without value or a value of another type raises."""
    with pytest.raises(TokeoPocketBaseError, match=message):
        build_filter(expr, params)


def test_filter_of_the_client_is_build_filter(pb):
    """pb.filter builds the same filter as build_filter."""
    assert pb.filter('t = {:t}', {'t': "it's"}) == build_filter('t = {:t}', {'t': "it's"})


# Record and ListResult


def test_record_reads_and_writes_fields_as_attributes():
    """record.title and record['title'] are the same field, also when set."""
    record = Record({'id': 'r1', 'title': 'Hello'})
    record.views = 4
    assert (record.title, record['views'], dict(record)) == ('Hello', 4, {'id': 'r1', 'title': 'Hello', 'views': 4})


def test_record_missing_field_raises_attribute_error():
    """A missing field read as attribute raises AttributeError."""
    with pytest.raises(AttributeError, match='nope'):
        Record({}).nope


def test_record_expand_holds_records():
    """The expanded relations are records, one or a list of them."""
    record = Record({'expand': {'owner': {'name': 'Anna'}, 'tags': [{'name': 'a'}, {'name': 'b'}]}})
    assert record.expand.owner.name == 'Anna'
    assert [tag.name for tag in record.expand.tags] == ['a', 'b']


def test_list_result_maps_the_page_and_its_records():
    """A list answer becomes page, per_page, total counts and records."""
    result = ListResult({'page': 2, 'perPage': 1, 'totalItems': 3, 'totalPages': 3, 'items': [{'id': 'r2'}]})
    assert (result.page, result.per_page, result.total_items, result.total_pages) == (2, 1, 3, 3)
    assert isinstance(result.items[0], Record) and result.items[0].id == 'r2'


# send


def test_send_adds_the_token_and_leaves_out_empty_params(pb, server):
    """A set token goes as Authorization header, params of None are left out."""
    pb.token = 'tok'
    pb.send('GET', '/api/x', params={'a': 1, 'b': None})
    assert server.requests[0].headers['Authorization'] == 'tok'
    assert _params(server.requests[0]) == {'a': '1'}


def test_send_without_token_sends_no_authorization(pb, server):
    """An anonymous client and an Authorization of None send no header."""
    pb.send('GET', '/api/x')
    pb.token = 'tok'
    pb.send('GET', '/api/x', headers={'Authorization': None})
    assert ['Authorization' in request.headers for request in server.requests] == [False, False]


def test_send_empty_answer_is_none(pb, server):
    """An answer without body, e.g. 204, gives None."""
    server.answers[('DELETE', '/api/x')] = httpx.Response(204)
    assert pb.send('DELETE', '/api/x') is None


def test_send_error_answer_raises_with_status_message_and_data(pb, server):
    """A 400 raises TokeoPocketBaseError with status, url, message and data."""
    answer = {'status': 400, 'message': 'Failed to create record.', 'data': {'title': {'code': 'validation_required'}}}
    server.answers[('POST', '/api/x')] = httpx.Response(400, json=answer)
    with pytest.raises(TokeoPocketBaseError, match='failed with 400: Failed to create record.') as caught:
        pb.send('POST', '/api/x', json={})
    assert (caught.value.status, caught.value.url) == (400, f'{URL}/api/x')
    assert caught.value.data == {'title': {'code': 'validation_required'}}
    assert isinstance(caught.value, TokeoError)


def test_send_error_answer_without_json_names_its_text(pb, server):
    """An error answer that is no json names its text and has no data."""
    server.answers[('GET', '/api/x')] = httpx.Response(502, text='Bad Gateway')
    with pytest.raises(TokeoPocketBaseError, match='failed with 502: Bad Gateway') as caught:
        pb.send('GET', '/api/x')
    assert caught.value.data == {}


def test_send_failed_connection_raises_with_status_0(pb, server):
    """A connection error raises with status 0 and the httpx error as cause."""

    def refuse(request):
        raise httpx.ConnectError('connection refused', request=request)

    server.respond = refuse
    with pytest.raises(TokeoPocketBaseError, match='connection refused') as caught:
        pb.health()
    assert caught.value.status == 0 and isinstance(caught.value.__cause__, httpx.ConnectError)


# before_request


def test_before_request_runs_before_each_request_but_auth_and_health(server):
    """before_request runs before records, batch and send, not login or health."""
    calls = []
    server.answers[('POST', '/api/collections/users/auth-with-password')] = _auth('tok')
    server.answers[('POST', '/api/collections/users/auth-refresh')] = _auth('tok2')
    server.answers[('POST', '/api/batch')] = []
    with PocketBase(URL, before_request=lambda: calls.append(True), transport=httpx.MockTransport(server.handle)) as client:
        client.auth_with_password('users', 'anna@tokeo.test', 'pass')
        client.auth_refresh()
        client.health()
        assert calls == []
        client.collection('posts').get_list()
        client.batch().send()
        client.send('GET', '/api/x')
        assert len(calls) == 3


def test_before_request_may_log_in(server):
    """A before_request that logs in runs once, its login does not call it again."""
    server.answers[('POST', '/api/collections/users/auth-with-password')] = _auth('tok')
    calls = []

    def log_in():
        calls.append(True)
        if not client.token:
            client.auth_with_password('users', 'anna@tokeo.test', 'pass')

    with PocketBase(URL, before_request=log_in, transport=httpx.MockTransport(server.handle)) as client:
        client.collection('posts').get_one('r1')
    assert len(calls) == 1
    assert server.requests[-1].headers['Authorization'] == 'tok'


def test_impersonate_runs_before_request_and_the_new_client_has_none(pb, server):
    """impersonate needs the login of before_request, the new client has no hook."""
    server.answers[('POST', '/api/collections/users/impersonate/u1')] = _auth('imp', 'u1')
    calls = []
    pb.before_request = lambda: calls.append(True)
    client = pb.impersonate('users', 'u1')
    assert len(calls) == 1 and client.before_request is None
    client.close()


# Collection


def test_collection_is_the_record_service_of_the_name(pb):
    """collection('posts') gives the record methods of posts, the same each time."""
    assert isinstance(pb.collection('posts'), Collection)
    assert pb.collection('posts') is pb.collection('posts')


def test_record_paths_quote_collection_and_id(pb, server):
    """A slash or a question mark in a name or an id stays inside its segment."""
    pb.collection('a/b').get_one('x?y')
    assert server.requests[0].url.raw_path == b'/api/collections/a%2Fb/records/x%3Fy'


def test_get_list_sends_the_options_as_query(pb, server):
    """get_list sends all its options and query as query parameters."""
    server.answers[('GET', '/api/collections/posts/records')] = {'page': 2, 'perPage': 5, 'items': [{'id': 'r1'}]}
    result = pb.collection('posts').get_list(2, 5, 'views > 3', '-title', 'owner', 'id,title', True, {'x': 'y'})
    assert _params(server.requests[0]) == dict(
        page='2', perPage='5', filter='views > 3', sort='-title', expand='owner', fields='id,title', skipTotal='true', x='y'
    )
    assert (result.page, result.items[0].id) == (2, 'r1')


def test_get_list_defaults_send_page_1_of_30(pb, server):
    """By default get_list asks for page 1 of 30 without further options."""
    pb.collection('posts').get_list()
    assert _params(server.requests[0]) == dict(page='1', perPage='30')


def test_get_list_query_wins_over_the_arguments(pb, server):
    """A key of query that is also an argument wins."""
    pb.collection('posts').get_list(sort='title', query={'sort': '-created'})
    assert _params(server.requests[0])['sort'] == '-created'


def test_get_full_list_pages_until_a_page_is_not_full(pb, server):
    """get_full_list pages with skipTotal until a page has fewer items."""
    pages = {'1': [{'id': 'a'}, {'id': 'b'}], '2': [{'id': 'c'}]}

    def respond(request):
        return httpx.Response(200, json={'items': pages[request.url.params['page']]})

    server.respond = respond
    records = pb.collection('posts').get_full_list(per_page=2, filter='views > 0')
    assert [record.id for record in records] == ['a', 'b', 'c']
    assert [(_params(r)['page'], _params(r)['perPage'], _params(r)['skipTotal']) for r in server.requests] == [
        ('1', '2', 'true'),
        ('2', '2', 'true'),
    ]


def test_get_first_gives_the_first_match(pb, server):
    """get_first asks for one record without total and gives it."""
    server.answers[('GET', '/api/collections/posts/records')] = {'items': [{'id': 'r1'}]}
    assert pb.collection('posts').get_first("title = 'a'").id == 'r1'
    assert _params(server.requests[0]) == dict(page='1', perPage='1', filter="title = 'a'", skipTotal='true')


def test_get_first_without_match_raises_404(pb, server):
    """get_first without a matching record raises with status 404."""
    server.answers[('GET', '/api/collections/posts/records')] = {'items': []}
    with pytest.raises(TokeoPocketBaseError, match='no record of posts matches') as caught:
        pb.collection('posts').get_first("title = 'a'")
    assert caught.value.status == 404


def test_get_one_gets_the_record_by_id(pb, server):
    """get_one sends GET .../records/r1 with expand and fields."""
    server.answers[('GET', '/api/collections/posts/records/r1')] = {'id': 'r1', 'title': 'Hello'}
    record = pb.collection('posts').get_one('r1', expand='owner', fields='id,title')
    assert _params(server.requests[0]) == dict(expand='owner', fields='id,title')
    assert isinstance(record, Record) and record.title == 'Hello'


def test_create_posts_the_fields_as_json(pb, server):
    """create sends POST .../records with the fields as json body."""
    server.answers[('POST', '/api/collections/posts/records')] = {'id': 'r1', 'title': 'New'}
    record = pb.collection('posts').create({'title': 'New'}, fields='id,title')
    assert _body(server.requests[0]) == {'title': 'New'} and _params(server.requests[0]) == dict(fields='id,title')
    assert record.id == 'r1'


def test_create_with_files_posts_multipart_with_the_fields_as_json_payload(pb, server):
    """With files create sends multipart, the fields as @jsonPayload."""
    pb.collection('posts').create({'title': 'New', 'meta': {'a': 1}}, files=[('doc', ('a.txt', b'hello', 'text/plain'))])
    request = server.requests[0]
    assert request.headers['Content-Type'].startswith('multipart/form-data')
    content = request.content.decode()
    assert 'name="@jsonPayload"' in content and '{"title": "New", "meta": {"a": 1}}' in content
    assert 'name="doc"; filename="a.txt"' in content and 'hello' in content


def test_update_patches_the_fields(pb, server):
    """update sends PATCH .../records/r1 with the fields, {} without fields."""
    pb.collection('posts').update('r1', {'views': 4})
    pb.collection('posts').update('r1')
    assert [(r.method, r.url.path, _body(r)) for r in server.requests] == [
        ('PATCH', '/api/collections/posts/records/r1', {'views': 4}),
        ('PATCH', '/api/collections/posts/records/r1', {}),
    ]


def test_delete_removes_the_record_and_gives_true(pb, server):
    """delete sends DELETE .../records/r1 and gives True."""
    server.answers[('DELETE', '/api/collections/posts/records/r1')] = httpx.Response(204)
    assert pb.collection('posts').delete('r1') is True
    assert server.requests[0].method == 'DELETE'


# auth


def test_auth_with_password_keeps_the_token_and_record(pb, server):
    """A login keeps token, record and collection, the login sends no old token."""
    server.answers[('POST', '/api/collections/users/auth-with-password')] = _auth('tok')
    pb.token = 'old'
    record = pb.auth_with_password('users', 'anna@tokeo.test', 'pass')
    assert _body(server.requests[0]) == {'identity': 'anna@tokeo.test', 'password': 'pass'}
    assert 'Authorization' not in server.requests[0].headers
    assert (pb.token, pb.auth_collection, record.email, pb.auth_record is record) == ('tok', 'users', 'anna@tokeo.test', True)


@pytest.mark.parametrize('status', [400, 401, 403])
def test_auth_with_password_refused_raises_auth_error(pb, server, status):
    """A refused login raises TokeoPocketBaseAuthError, no token is kept."""
    server.answers[('POST', '/api/collections/users/auth-with-password')] = httpx.Response(
        status, json={'message': 'Failed to authenticate.'}
    )
    with pytest.raises(TokeoPocketBaseAuthError, match='Failed to authenticate.') as caught:
        pb.auth_with_password('users', 'anna@tokeo.test', 'bad')
    assert caught.value.status == status and pb.token is None


def test_auth_refresh_renews_the_token_of_the_login(pb, server):
    """auth_refresh sends the token of the login and keeps the new one."""
    server.answers[('POST', '/api/collections/users/auth-with-password')] = _auth('tok1')
    server.answers[('POST', '/api/collections/users/auth-refresh')] = _auth('tok2')
    pb.auth_with_password('users', 'anna@tokeo.test', 'pass')
    pb.auth_refresh()
    assert server.requests[1].headers['Authorization'] == 'tok1' and pb.token == 'tok2'


def test_auth_refresh_without_login_raises_auth_error(pb, server):
    """auth_refresh without a login raises and sends nothing."""
    with pytest.raises(TokeoPocketBaseAuthError, match='needs a login first'):
        pb.auth_refresh()
    assert server.requests == []


def test_auth_refresh_refused_raises_auth_error(pb, server):
    """A refused refresh raises TokeoPocketBaseAuthError with status 401."""
    server.answers[('POST', '/api/collections/users/auth-with-password')] = _auth('tok1')
    server.answers[('POST', '/api/collections/users/auth-refresh')] = httpx.Response(401, json={'message': 'expired'})
    pb.auth_with_password('users', 'anna@tokeo.test', 'pass')
    with pytest.raises(TokeoPocketBaseAuthError) as caught:
        pb.auth_refresh()
    assert caught.value.status == 401


def test_impersonate_gives_a_client_of_its_own_with_the_token(pb, server):
    """impersonate gives a new client with the token of the record."""
    server.answers[('POST', '/api/collections/users/impersonate/u1')] = _auth('imp', 'u1')
    pb.token = 'super'
    client = pb.impersonate('users', 'u1', duration=60)
    assert _body(server.requests[0]) == {'duration': 60} and server.requests[0].headers['Authorization'] == 'super'
    assert (client.token, client.auth_collection, client.auth_record.id, pb.token) == ('imp', 'users', 'u1', 'super')
    assert client.http_client is not pb.http_client
    client.collection('posts').get_list()
    assert server.requests[1].headers['Authorization'] == 'imp'
    client.close()


def test_impersonate_refused_raises_auth_error(pb, server):
    """impersonate without a superuser login raises with status 403."""
    server.answers[('POST', '/api/collections/users/impersonate/u1')] = httpx.Response(403, json={'message': 'not allowed'})
    with pytest.raises(TokeoPocketBaseAuthError) as caught:
        pb.impersonate('users', 'u1')
    assert caught.value.status == 403


@pytest.mark.parametrize(
    'token, seconds, valid',
    [(None, 0, False), ('broken', 0, False), (_jwt(None), 0, False), (_jwt(3600), 60, True), (_jwt(30), 60, False)],
    ids=['none', 'broken', 'no_exp', 'valid', 'too_short'],
)
def test_token_valid_tells_whether_the_token_lasts(pb, token, seconds, valid):
    """token_valid is True only for a token valid longer than the given seconds."""
    pb.token = token
    assert pb.token_valid(seconds) is valid


def test_close_forgets_the_login_and_closes_the_client(server):
    """Leaving the with block clears the login and closes the http client."""
    with PocketBase(URL, token='tok', transport=httpx.MockTransport(server.handle)) as client:
        pass
    assert (client.token, client.auth_record, client.auth_collection) == (None, None, None)
    assert client.http_client.is_closed


# batch and health


def test_batch_sends_all_changes_in_one_request(pb, server):
    """The changes go as requests of one POST /api/batch, the answers as records."""
    server.answers[('POST', '/api/batch')] = [
        {'status': 200, 'body': {'id': 'a'}},
        {'status': 200, 'body': {'id': 'b'}},
        {'status': 200, 'body': {'id': 'c'}},
        {'status': 204, 'body': None},
    ]
    batch = pb.batch()
    assert isinstance(batch, Batch)
    batch.create('posts', {'title': 'A'}).update('posts', 'b', {'views': 1}).upsert('posts', {'id': 'c'}).delete('posts', 'd')
    results = batch.send()
    assert _body(server.requests[0]) == {
        'requests': [
            {'method': 'POST', 'url': '/api/collections/posts/records', 'body': {'title': 'A'}},
            {'method': 'PATCH', 'url': '/api/collections/posts/records/b', 'body': {'views': 1}},
            {'method': 'PUT', 'url': '/api/collections/posts/records', 'body': {'id': 'c'}},
            {'method': 'DELETE', 'url': '/api/collections/posts/records/d'},
        ]
    }
    assert [result and result.id for result in results] == ['a', 'b', 'c', None]


def test_batch_failed_raises_with_the_failed_request(pb, server):
    """A failed batch raises with the failed request in data."""
    failed = {'requests': {'1': {'code': 'batch_request_failed', 'response': {'status': 404}}}}
    server.answers[('POST', '/api/batch')] = httpx.Response(400, json={'message': 'Batch transaction failed.', 'data': failed})
    with pytest.raises(TokeoPocketBaseError, match='Batch transaction failed.') as caught:
        pb.batch().create('posts', {}).delete('posts', 'x').send()
    assert caught.value.data['requests']['1']['response']['status'] == 404


def test_health_gives_the_answer_of_the_server(pb, server):
    """health sends GET /api/health and gives its answer."""
    server.answers[('GET', '/api/health')] = {'code': 200, 'message': 'API is healthy.'}
    assert pb.health()['code'] == 200
