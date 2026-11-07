"""
The client of one PocketBase server with one login, and the keeper of a
login that confirms it on the server.

"""

import json
import time
import base64
import threading
import httpx
from tokeo.core.utils.jwt import is_token_valid
from .exc import TokeoPocketBaseError, TokeoPocketBaseAuthError
from .record import Record
from .filter import format_filter, quote_path_segment
from .collection import Collection
from .batch import Batch


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
        ```auth_refresh```, their requests do not call it again; an
        ```AuthKeeper``` keeps a login valid this way

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
        #: The number of logins of this client, a refresh does not count; a
        #: realtime channel sends its subscriptions again after a new login
        self.login_cnt = 0
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

    # ---- requests -----------------------------------------------------------

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
                result = response.json()
            except ValueError:
                result = {}
            message = result.get('message') if isinstance(result, dict) else None
            raise TokeoPocketBaseError(
                f'{method} {url} failed with {response.status_code}: {message or response.text[:200]}',
                status=response.status_code,
                url=url,
                data=result.get('data') if isinstance(result, dict) else None,
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
        return format_filter(expr, params)

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

    def create_batch(self):
        """
        Start a batch of record changes sent in one transaction.

        ### Returns

        - **Batch**: The batch to add the changes to

        """
        return Batch(self)

    def check_health(self):
        """
        Check the health of the server.

        ### Returns

        - **dict**: The answer, ```code``` 200 when healthy

        ### Raises

        - **TokeoPocketBaseError**: If the server is not reachable or not
            healthy

        """
        return self._send('GET', '/api/health')

    # ---- auth ---------------------------------------------------------------

    def _set_auth(self, collection, result):
        # keep the token and the record of a login
        self.token = result['token']
        self.auth_record = Record(result.get('record'))
        self.auth_collection = collection
        return self.auth_record

    def clear_auth(self):
        """Forget the current login, the next requests are anonymous."""
        self.token = None
        self.auth_record = None
        self.auth_collection = None

    def is_token_valid(self, min_valid_seconds=0):
        """
        Tell whether a token is set and valid for some more seconds.

        ### Args

        - **min_valid_seconds** (int): The seconds the token must stay valid

        ### Returns

        - **bool**: True for a token valid longer than min_valid_seconds; a
            token without ```exp``` or a broken one is not valid

        """
        return bool(self.token) and is_token_valid(self.token, min_valid_seconds)

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
        path = f'/api/collections/{quote_path_segment(collection)}/auth-with-password'
        try:
            result = self._send('POST', path, json={'identity': identity, 'password': password}, headers={'Authorization': None})
        except TokeoPocketBaseError as err:
            if err.status in (400, 401, 403):
                raise TokeoPocketBaseAuthError(str(err), status=err.status, url=err.url, data=err.data) from err
            raise
        self.login_cnt += 1
        return self._set_auth(collection, result)

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

        : A token given from elsewhere, without a login of this client, is
            refreshed on the collection its ```collectionId``` names

        """
        collection = self.auth_collection
        if self.token and not collection:
            # a token from elsewhere names its collection by id
            try:
                payload = self.token.split('.')[1]
                collection = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))).get('collectionId')
            except (IndexError, ValueError, AttributeError):
                collection = None
        if not self.token or not collection:
            raise TokeoPocketBaseAuthError('auth_refresh needs a login first')
        path = f'/api/collections/{quote_path_segment(collection)}/auth-refresh'
        try:
            result = self._send('POST', path)
        except TokeoPocketBaseError as err:
            if err.status in (401, 403, 404):
                raise TokeoPocketBaseAuthError(str(err), status=err.status, url=err.url, data=err.data) from err
            raise
        return self._set_auth(collection, result)

    def impersonate(self, collection, record_id, duration=0, client=None):
        """
        Get a client that acts as another record, by a superuser login.

        ### Args

        - **collection** (str): The auth collection of the record
        - **record_id** (str): The id of the record to act as
        - **duration** (int): The seconds the token is valid, 0 takes the
            default of the collection
        - **client** (PocketBase): The client that takes the login, e.g. to
            renew it; None makes a new one

        ### Returns

        - **PocketBase**: The client with the token of the record, a new one
            with its own connections unless given

        ### Raises

        - **TokeoPocketBaseAuthError**: If this client is no superuser login
            or the record is unknown

        ### Notes

        : A new client is closed separately; its token cannot be extended by
            a refresh, and it has no ```before_request```

        : A refresh of the token still confirms on the server that the login
            holds, it answers with the same token

        """
        path = f'/api/collections/{quote_path_segment(collection)}/impersonate/{quote_path_segment(record_id)}'
        try:
            result = self.send('POST', path, json={'duration': duration})
        except TokeoPocketBaseError as err:
            if err.status in (401, 403, 404):
                raise TokeoPocketBaseAuthError(str(err), status=err.status, url=err.url, data=err.data) from err
            raise
        client = client or PocketBase(self.url, **self._httpx_kwargs)
        client.login_cnt += 1
        client._set_auth(collection, result)
        return client


class AuthKeeper:
    """
    Keep the login of a client valid, as its ```before_request```.

    ### Args

    - **client** (PocketBase): The client whose login is kept
    - **login** (callable): Called without arguments to log the client in
        anew, e.g. by ```auth_with_password```; raises when it cannot

    ### Keyword Args

    - **min_valid_seconds** (int): Renew a token that runs out within these
        seconds
    - **check_seconds** (float): Ask the server again after these seconds
        whether the login still holds

    ### Example

    ```python
    pb = PocketBase('http://127.0.0.1:8090')

    def login():
        pb.auth_with_password('users', 'service@example.com', 'secret')

    pb.before_request = AuthKeeper(pb, login)
    ```

    ### Notes

    : PocketBase takes a refused token without an error, as a guest; a token
        valid by its expiry may already be refused, e.g. after a change of the
        password or of a deleted record. The keeper asks the server by
        ```auth_refresh()``` every ```check_seconds``` and logs in anew when
        it refuses, the error of a failed login raises

    : One lock guards the check, the requests of several threads share the
        login

    """

    def __init__(self, client, login, min_valid_seconds=60, check_seconds=60.0):
        #: The client whose login is kept
        self.client = client
        #: Logs the client in anew
        self.login = login
        #: Renew a token that runs out within these seconds
        self.min_valid_seconds = min_valid_seconds
        #: Ask the server again after these seconds whether the login holds
        self.check_seconds = check_seconds
        #: Held while the login is checked or renewed
        self.lock = threading.Lock()
        # when the server confirmed the login last, by time.monotonic()
        self._last_check_time = None
        # the login count of the client at the last check
        self._login_cnt = None

    def __call__(self):
        with self.lock:
            # a login since the last check, e.g. a new impersonation, comes
            # just from the server; a token from elsewhere is no login
            if self.client.login_cnt and self.client.login_cnt != self._login_cnt:
                self._login_cnt, self._last_check_time = self.client.login_cnt, time.monotonic()
            is_fresh = self._last_check_time is not None and time.monotonic() - self._last_check_time < self.check_seconds
            if is_fresh and self.client.is_token_valid(self.min_valid_seconds):
                return
            if self.client.is_token_valid(0) and self.client.auth_collection:
                token = self.client.token
                try:
                    # the server confirms the login and renews a refreshable token
                    self.client.auth_refresh()
                except TokeoPocketBaseAuthError:
                    # refused, e.g. after a change of the password
                    pass
                else:
                    # an impersonation answers with its same token, near its
                    # expiry only a new login renews it
                    if self.client.token != token or self.client.is_token_valid(self.min_valid_seconds):
                        self._last_check_time = time.monotonic()
                        return
            self.login()
            self._login_cnt, self._last_check_time = self.client.login_cnt, time.monotonic()
