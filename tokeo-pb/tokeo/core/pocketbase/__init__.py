"""
Tokeo PocketBase Module.

A driver for the REST API of PocketBase 0.40 and later, built on httpx. It
reads and writes records, builds filters with safely quoted values, logs in
with a password, refreshes and impersonates, sends batches and checks the
health of the server.

### Example

```python
from tokeo.core.pocketbase import PocketBase

with PocketBase('http://127.0.0.1:8090') as pb:
    pb.auth_with_password('users', 'service@example.com', 'secret')
    posts = pb.collection('posts')
    post = posts.create({'title': 'Hello', 'views': 0})
    found = posts.get_list(filter=pb.filter('title = {:title}', {'title': "it's"}))
    print(post.id, found.total_items)
```

### Notes

: Each PocketBase instance holds its own http client and its own token, an
    instance logged in as one identity never acts for another one

: Every error of the server or of the connection raises
    ```TokeoPocketBaseError``` with the status (0 without an answer), the
    url and the data of the answer

: Build a filter from outside values only by ```filter()``` with
    placeholders; it quotes and escapes the values for the filter syntax

"""

import re
import json
from datetime import datetime, timezone
from urllib.parse import quote
import httpx
from tokeo.core.exc import TokeoError
from tokeo.core.utils.jwt import token_expires_within


class TokeoPocketBaseError(TokeoError):
    """
    Raised for an error answer of PocketBase or a failed request.

    ### Args

    - **message** (str): What failed
    - **status** (int): The http status, 0 when no answer came
    - **url** (str): The url of the request
    - **data** (dict): The data of the error answer, e.g. the invalid fields

    """

    def __init__(self, message, status=0, url=None, data=None):
        super().__init__(message)
        #: The http status, 0 when no answer came
        self.status = status
        #: The url of the request
        self.url = url
        #: The data of the error answer, e.g. the invalid fields
        self.data = data if isinstance(data, dict) else {}


class TokeoPocketBaseAuthError(TokeoPocketBaseError):
    """Raised when an authentication cannot be done."""


class Record(dict):
    """
    A record of PocketBase, a dict that also reads and writes its fields as
    attributes.

    ### Notes

    : ```record.title``` and ```record['title']``` are the same; a field
        named like a dict method, e.g. ```items```, is only reached by
        ```record['items']```

    : The records in ```expand``` are records as well

    """

    def __init__(self, data=None):
        super().__init__(data or {})
        # the expanded relations are records too, one or a list of them
        expand = self.get('expand')
        if isinstance(expand, dict):
            self['expand'] = Record(
                {key: [Record(item) for item in value] if isinstance(value, list) else Record(value) for key, value in expand.items()}
            )

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


class ListResult:
    """
    A page of records.

    ### Args

    - **data** (dict): The answer of PocketBase for a list of records

    ### Notes

    : ```total_items``` and ```total_pages``` are -1 when the count was
        skipped by ```skip_total```

    """

    def __init__(self, data):
        #: The number of this page, from 1
        self.page = data.get('page', 1)
        #: The number of records per page
        self.per_page = data.get('perPage', 0)
        #: The number of records of all pages, -1 when skipped
        self.total_items = data.get('totalItems', -1)
        #: The number of pages, -1 when skipped
        self.total_pages = data.get('totalPages', -1)
        #: The records of this page
        self.items = [Record(item) for item in data.get('items') or []]


# a placeholder of a filter: {:name}
_PLACEHOLDER = re.compile(r'\{:(\w+)\}')


def _filter_value(value):
    # a value as literal of the filter syntax of PocketBase; bool before int,
    # bool is an int in python
    if isinstance(value, str):
        # the filter parser knows the escapes \\ and \' in a quoted string
        return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, datetime):
        # pocketbase stores datetimes in utc, a naive one is taken as utc
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return "'" + value.strftime('%Y-%m-%d %H:%M:%S.') + f'{value.microsecond // 1000:03d}Z' + "'"
    raise TokeoPocketBaseError(f'filter value of type {type(value).__name__} is not supported: {value!r}')


def build_filter(expr, params=None):
    """
    Build a filter expression with its placeholders replaced by values.

    ### Args

    - **expr** (str): The filter with placeholders like ```{:title}```
    - **params** (dict): The values of the placeholders

    ### Returns

    - **str**: The filter with each value quoted and escaped

    ### Raises

    - **TokeoPocketBaseError**: If a placeholder has no value or a value is
        of an unsupported type

    ### Notes

    : Supported values: str, bool, None, int, float and datetime

    : Each placeholder is replaced once, a value that looks like a
        placeholder is never replaced again

    ### Example

    ```python
    build_filter('title = {:t} && views > {:v}', {'t': "it's", 'v': 3})
    # title = 'it\\'s' && views > 3
    ```

    """
    params = params or {}

    def replace(match):
        name = match.group(1)
        if name not in params:
            raise TokeoPocketBaseError(f'filter placeholder {{:{name}}} has no value')
        return _filter_value(params[name])

    return _PLACEHOLDER.sub(replace, expr)


def _segment(value):
    # one segment of an url path, quoted so it cannot leave its place
    return quote(str(value), safe='')


class PocketBase:
    """
    A client of the REST API of one PocketBase server.

    ### Args

    - **url** (str): The url of the server, e.g. ```http://127.0.0.1:8090```

    ### Keyword Args

    - **token** (str): A token to start with, e.g. of another login
    - **before_request** (callable): Called without arguments before each
        request but login, refresh and health, e.g. to log in on first use
    - **httpx_kwargs**: Further options of the httpx client, e.g.
        ```timeout```, ```verify```, ```limits``` or ```transport```

    ### Notes

    : Use it as context manager or call ```close()``` to free its
        connections

    : ```before_request``` may log in by ```auth_with_password``` or
        ```auth_refresh```, their requests do not call it again

    """

    def __init__(self, url, token=None, before_request=None, **httpx_kwargs):
        #: The url of the server
        self.url = url.rstrip('/')
        #: The token of the current login, None while anonymous
        self.token = token
        #: Called before each request but login, refresh and health
        self.before_request = before_request
        #: The record of the current login
        self.auth_record = None
        #: The auth collection of the current login
        self.auth_collection = None
        # kept to build the client of an impersonation the same way
        self._httpx_kwargs = httpx_kwargs
        #: The httpx client of the requests
        self.http_client = httpx.Client(base_url=self.url, **httpx_kwargs)
        self._collections = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        """Forget the login and close the connections of the http client."""
        self.clear_auth()
        self.http_client.close()

    # ----------------------------------------------------------------------------------
    # requests
    # ----------------------------------------------------------------------------------

    def send(self, method, path, params=None, json=None, data=None, files=None, headers=None):
        """
        Send a request to the api and return its answer.

        ### Args

        - **method** (str): The http method, e.g. ```GET```
        - **path** (str): The path of the api, e.g. ```/api/health```
        - **params** (dict): The query parameters
        - **json** (any): The body as json
        - **data** (dict): The form fields of a multipart body
        - **files** (list): The files of a multipart body, as httpx takes them
        - **headers** (dict): Further headers; an ```Authorization``` of None
            sends the request without the token

        ### Returns

        - **any**: The json of the answer, None for an empty answer

        ### Raises

        - **TokeoPocketBaseError**: For an error answer or a failed request

        """
        if self.before_request:
            self.before_request()
        return self._send(method, path, params, json, data, files, headers)

    def _send(self, method, path, params=None, json=None, data=None, files=None, headers=None):
        # the request itself, without before_request
        headers = dict(headers or {})
        if self.token and 'Authorization' not in headers:
            headers['Authorization'] = self.token
        headers = {key: value for key, value in headers.items() if value is not None}
        # leave out the parameters without value
        params = {key: value for key, value in (params or {}).items() if value is not None}
        url = f'{self.url}{path}'
        try:
            response = self.http_client.request(method, path, params=params, json=json, data=data, files=files, headers=headers)
        except httpx.HTTPError as err:
            raise TokeoPocketBaseError(f'{method} {url} failed: {err}', status=0, url=url) from err
        if response.status_code >= 400:
            try:
                answer = response.json()
            except ValueError:
                answer = {}
            message = answer.get('message') if isinstance(answer, dict) else None
            raise TokeoPocketBaseError(
                f'{method} {url} failed with {response.status_code}: {message or response.text[:200]}',
                status=response.status_code,
                url=url,
                data=answer.get('data') if isinstance(answer, dict) else None,
            )
        return response.json() if response.content else None

    def filter(self, expr, params=None):
        """
        Build a filter expression with its placeholders replaced by values.

        ### Args

        - **expr** (str): The filter with placeholders like ```{:title}```
        - **params** (dict): The values of the placeholders

        ### Returns

        - **str**: The filter with each value quoted and escaped

        """
        return build_filter(expr, params)

    def collection(self, name):
        """
        Get the records of a collection.

        ### Args

        - **name** (str): The name or id of the collection

        ### Returns

        - **Collection**: The record methods of the collection

        """
        if name not in self._collections:
            self._collections[name] = Collection(self, name)
        return self._collections[name]

    def batch(self):
        """
        Start a batch of record changes sent in one transaction.

        ### Returns

        - **Batch**: The batch to add the changes to

        """
        return Batch(self)

    def health(self):
        """
        Check the health of the server.

        ### Returns

        - **dict**: The answer, ```code``` 200 when healthy

        ### Raises

        - **TokeoPocketBaseError**: If the server is not reachable or not
            healthy

        """
        return self._send('GET', '/api/health')

    # ----------------------------------------------------------------------------------
    # auth
    # ----------------------------------------------------------------------------------

    def _set_auth(self, collection, answer):
        # keep the token and the record of a login
        self.token = answer['token']
        self.auth_record = Record(answer.get('record'))
        self.auth_collection = collection
        return self.auth_record

    def clear_auth(self):
        """Forget the current login, the next requests are anonymous."""
        self.token = None
        self.auth_record = None
        self.auth_collection = None

    def token_valid(self, min_valid_seconds=0):
        """
        Tell whether a token is set and valid for some more seconds.

        ### Args

        - **min_valid_seconds** (int): The seconds the token must stay valid

        ### Returns

        - **bool**: True for a token valid longer than min_valid_seconds; a
            token without ```exp``` or a broken one is not valid

        """
        return bool(self.token) and not token_expires_within(self.token, min_valid_seconds)

    def auth_with_password(self, collection, identity, password):
        """
        Log in with an identity and its password.

        ### Args

        - **collection** (str): The auth collection, e.g. ```users``` or
            ```_superusers```
        - **identity** (str): The identity, e.g. the email
        - **password** (str): The password

        ### Returns

        - **Record**: The record of the login, the token is kept

        ### Raises

        - **TokeoPocketBaseAuthError**: If the login is refused
        - **TokeoPocketBaseError**: If the request fails otherwise

        """
        path = f'/api/collections/{_segment(collection)}/auth-with-password'
        try:
            answer = self._send('POST', path, json={'identity': identity, 'password': password}, headers={'Authorization': None})
        except TokeoPocketBaseError as err:
            if err.status in (400, 401, 403):
                raise TokeoPocketBaseAuthError(str(err), status=err.status, url=err.url, data=err.data) from err
            raise
        return self._set_auth(collection, answer)

    def auth_refresh(self):
        """
        Renew the token of the current login.

        ### Returns

        - **Record**: The record of the login, the new token is kept

        ### Raises

        - **TokeoPocketBaseAuthError**: If there is no login or the token is
            refused

        ### Notes

        : The token of an impersonation keeps its expiry, a refresh does
            not extend it

        """
        if not self.token or not self.auth_collection:
            raise TokeoPocketBaseAuthError('auth_refresh needs a login first')
        path = f'/api/collections/{_segment(self.auth_collection)}/auth-refresh'
        try:
            answer = self._send('POST', path)
        except TokeoPocketBaseError as err:
            if err.status in (401, 403, 404):
                raise TokeoPocketBaseAuthError(str(err), status=err.status, url=err.url, data=err.data) from err
            raise
        return self._set_auth(self.auth_collection, answer)

    def impersonate(self, collection, record_id, duration=0):
        """
        Get a client that acts as another record, by a superuser login.

        ### Args

        - **collection** (str): The auth collection of the record
        - **record_id** (str): The id of the record to act as
        - **duration** (int): The seconds the token is valid, 0 takes the
            default of the collection

        ### Returns

        - **PocketBase**: A new client with its own connections and the token
            of the record

        ### Raises

        - **TokeoPocketBaseAuthError**: If this client is no superuser login
            or the record is unknown

        ### Notes

        : The new client is closed separately; its token cannot be extended
            by a refresh, and it has no ```before_request```

        """
        path = f'/api/collections/{_segment(collection)}/impersonate/{_segment(record_id)}'
        try:
            answer = self.send('POST', path, json={'duration': duration})
        except TokeoPocketBaseError as err:
            if err.status in (401, 403, 404):
                raise TokeoPocketBaseAuthError(str(err), status=err.status, url=err.url, data=err.data) from err
            raise
        client = PocketBase(self.url, **self._httpx_kwargs)
        client._set_auth(collection, answer)
        return client


class Collection:
    """
    The record methods of one collection.

    ### Args

    - **client** (PocketBase): The client that sends the requests
    - **collection** (str): The name or id of the collection

    ### Notes

    : ```query``` takes further query parameters of PocketBase; a key there
        wins over the arguments

    """

    def __init__(self, client, collection):
        #: The client that sends the requests
        self.client = client
        #: The name or id of the collection
        self.collection = collection
        self._path = f'/api/collections/{_segment(collection)}/records'

    def _record_path(self, record_id):
        # the path of one record
        return f'{self._path}/{_segment(record_id)}'

    def get_list(self, page=1, per_page=30, filter=None, sort=None, expand=None, fields=None, skip_total=False, query=None):
        """
        Get a page of records.

        ### Args

        - **page** (int): The page, from 1
        - **per_page** (int): The records per page
        - **filter** (str): The filter, e.g. built by ```filter()```
        - **sort** (str): The sort order, e.g. ```-created,title```
        - **expand** (str): The relations to expand, e.g. ```owner```
        - **fields** (str): The fields to return, e.g. ```id,title```
        - **skip_total** (bool): Skip counting all records, faster
        - **query** (dict): Further query parameters

        ### Returns

        - **ListResult**: The records of the page and the counts

        """
        params = {
            'page': page,
            'perPage': per_page,
            'filter': filter or None,
            'sort': sort or None,
            'expand': expand or None,
            'fields': fields or None,
            'skipTotal': 'true' if skip_total else None,
            **(query or {}),
        }
        return ListResult(self.client.send('GET', self._path, params=params))

    def get_full_list(self, per_page=500, filter=None, sort=None, expand=None, fields=None, query=None):
        """
        Get all records, page by page.

        ### Args

        - **per_page** (int): The records per request
        - **filter** (str): The filter, e.g. built by ```filter()```
        - **sort** (str): The sort order
        - **expand** (str): The relations to expand
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **list**: All records that match

        """
        records = []
        page = 1
        while True:
            result = self.get_list(page, per_page, filter, sort, expand, fields, skip_total=True, query=query)
            records.extend(result.items)
            if len(result.items) < per_page:
                return records
            page += 1

    def get_first(self, filter, sort=None, expand=None, fields=None, query=None):
        """
        Get the first record that matches a filter.

        ### Args

        - **filter** (str): The filter, e.g. built by ```filter()```
        - **sort** (str): The sort order
        - **expand** (str): The relations to expand
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **Record**: The first record

        ### Raises

        - **TokeoPocketBaseError**: With status 404 if no record matches

        """
        result = self.get_list(1, 1, filter, sort, expand, fields, skip_total=True, query=query)
        if not result.items:
            raise TokeoPocketBaseError(f'no record of {self.collection} matches {filter!r}', status=404)
        return result.items[0]

    def get_one(self, record_id, expand=None, fields=None, query=None):
        """
        Get one record by its id.

        ### Args

        - **record_id** (str): The id of the record
        - **expand** (str): The relations to expand
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **Record**: The record

        ### Raises

        - **TokeoPocketBaseError**: With status 404 if the record is unknown

        """
        params = {'expand': expand or None, 'fields': fields or None, **(query or {})}
        return Record(self.client.send('GET', self._record_path(record_id), params=params))

    def _write(self, method, path, data, files, expand, fields, query):
        # json, or multipart with the fields as @jsonPayload when files go along
        params = {'expand': expand or None, 'fields': fields or None, **(query or {})}
        if files:
            answer = self.client.send(method, path, params=params, data={'@jsonPayload': json.dumps(data or {})}, files=files)
        else:
            answer = self.client.send(method, path, params=params, json=data or {})
        return Record(answer)

    def create(self, data=None, files=None, expand=None, fields=None, query=None):
        """
        Create a record.

        ### Args

        - **data** (dict): The fields of the record
        - **files** (list): Files to upload, as ```(field, (filename,
            content, content_type))``` tuples
        - **expand** (str): The relations to expand in the answer
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **Record**: The new record

        """
        return self._write('POST', self._path, data, files, expand, fields, query)

    def update(self, record_id, data=None, files=None, expand=None, fields=None, query=None):
        """
        Update fields of a record.

        ### Args

        - **record_id** (str): The id of the record
        - **data** (dict): The fields to change
        - **files** (list): Files to upload, as ```(field, (filename,
            content, content_type))``` tuples
        - **expand** (str): The relations to expand in the answer
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **Record**: The changed record

        """
        return self._write('PATCH', self._record_path(record_id), data, files, expand, fields, query)

    def delete(self, record_id, query=None):
        """
        Delete a record.

        ### Args

        - **record_id** (str): The id of the record
        - **query** (dict): Further query parameters

        ### Returns

        - **bool**: True once deleted

        ### Raises

        - **TokeoPocketBaseError**: With status 404 if the record is unknown

        """
        self.client.send('DELETE', self._record_path(record_id), params=query)
        return True


class Batch:
    """
    Record changes sent together in one transaction.

    ### Args

    - **client** (PocketBase): The client that sends the batch

    ### Example

    ```python
    batch = pb.batch()
    batch.create('posts', {'title': 'A'})
    batch.update('posts', post_id, {'views': 4})
    batch.delete('posts', old_id)
    created, updated, deleted = batch.send()
    ```

    ### Notes

    : The batch api must be enabled in the settings of PocketBase

    : Either all changes are done or none, a failed one raises with the
        failed request in ```data['requests']```

    """

    def __init__(self, client):
        #: The client that sends the batch
        self.client = client
        #: The requests of the batch
        self.requests = []

    def _add(self, method, collection, record_id, data):
        # one request of the batch
        url = f'/api/collections/{_segment(collection)}/records'
        if record_id is not None:
            url = f'{url}/{_segment(record_id)}'
        request = {'method': method, 'url': url}
        if data is not None:
            request['body'] = data
        self.requests.append(request)
        return self

    def create(self, collection, data):
        """Add the creation of a record, returns the batch."""
        return self._add('POST', collection, None, data)

    def update(self, collection, record_id, data):
        """Add the change of a record, returns the batch."""
        return self._add('PATCH', collection, record_id, data)

    def upsert(self, collection, data):
        """Add the creation or change of a record by its id, returns the batch."""
        return self._add('PUT', collection, None, data)

    def delete(self, collection, record_id):
        """Add the deletion of a record, returns the batch."""
        return self._add('DELETE', collection, record_id, None)

    def send(self):
        """
        Send all changes in one transaction.

        ### Returns

        - **list**: The record of each change in order, None for a deletion

        ### Raises

        - **TokeoPocketBaseError**: If the batch is refused or a change fails,
            nothing is changed then

        """
        answers = self.client.send('POST', '/api/batch', json={'requests': self.requests})
        return [Record(answer['body']) if answer.get('body') is not None else None for answer in answers]
