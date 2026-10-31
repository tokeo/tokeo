"""
Tokeo PocketBase Extension Module.

This extension integrates PocketBase with Tokeo applications, providing a
lightweight database solution and backend services. PocketBase is a
zero-config backend built on SQLite that provides REST API, authentication,
and realtime subscriptions.

The extension exposes a Cement-style database handler, reachable as app.db
throughout your Tokeo application, that maps the usual CRUD methods onto
PocketBase collections. It runs on the driver of tokeo-pb
(```tokeo.core.pocketbase```), whose client is reached as app.db.pb for
anything beyond that surface.

### Features

- Seamless integration with Tokeo applications
- Simple CRUD operations for PocketBase collections
- Advanced querying, filtering, and sorting on lists
- User and auth collections handled like any other via CRUD
- Optional lazy service-account login (auto-refreshed, thread-safe) via the
  auth_identity/auth_password settings, before every request of the client
- The records of a collection by collection(), and the driver client by
  app.db.pb for filters, batches, impersonation and health

### Example

```python
    # Get data from a collection with filtering and sorting
    records = app.db.get_list(
        'products',
        filter='price > 100',
        sort='-created,name',
        per_page=50
    )

    # Create a new record
    new_user = app.db.create('users', {
        'username': 'example',
        'email': 'user@example.com',
        'password': 'securepassword',
        'passwordConfirm': 'securepassword'
    })

    # Update a record
    app.db.update('users', new_user.id, {
        'is_active': True
    })

    # Delete a record
    app.db.delete('products', 'record_id_to_delete')
```

"""

import threading
import httpx
from cement.core.meta import MetaMixin
from tokeo.core.utils.tls import create_ssl_context as tls_create_ssl_context
from tokeo.core.pocketbase import PocketBase, TokeoPocketBaseError, TokeoPocketBaseAuthError

# collections tried in order on the first login: a regular auth collection
# first, then the superuser collection as a fallback; the one that succeeds
# is remembered and reused for refresh and re-login
_AUTH_COLLECTIONS = ('users', '_superusers')


class TokeoPocketBaseHandler(MetaMixin):
    """
    PocketBase integration handler for Tokeo applications.

    A Cement-style database handler that maps the usual CRUD methods
    (get_one, get_list, create, update, delete) onto PocketBase
    collections. For anything outside that surface, collection() hands
    back the records of a collection and app.db.pb the driver client.

    ### Args

    - **app**: The Tokeo application instance
    - **args**: Variable length argument list passed to parent initializer

    ### Keyword Args

    - **kw**: Arbitrary keyword arguments passed to parent initializer

    ### Notes

    : The handler is registered as 'db' and is reached through app.db. It
        is a thin convenience layer over the driver of tokeo-pb.

    : Authentication: without auth_identity the handler connects
        anonymously, so the mapped CRUD calls run against whatever the
        PocketBase api rules permit for unauthenticated requests. With
        auth_identity and auth_password it logs in that service identity on
        first use and refreshes its token, see ensure_auth.

    : The handler holds one client with one login for the service
        identity; never log in other users on ```app.db.pb```, take a client
        of their own instead, e.g. by ```app.db.pb.impersonate(...)``` of a
        superuser login

    : This constructor only stores the application reference. The actual
        PocketBase client initialization is deferred until the _setup method
        is called by the framework to ensure proper configuration loading.

    """

    class Meta:
        """
        Handler meta-data configuration.

        ### Notes

        : This class defines the configuration section, default values,
            and unique identifier required by the Cement framework for
            proper handler registration and operation.
        """

        # Unique identifier for this handler
        label = 'tokeo.pocketbase'

        # Configuration section name in the application config
        config_section = 'pocketbase'

        # Default configuration settings
        config_defaults = dict(
            # URL for the PocketBase server; use https:// to enable tls,
            # verified against the system ca store by default (the secure option)
            url='http://127.0.0.1:8090',
            # username/identity for the service login; unset keeps the handler
            # anonymous (authentication is opt-in)
            auth_identity=None,
            # password for the service login
            auth_password=None,
            # refresh the token once fewer than this many seconds of validity
            # remain
            auth_token_min_valid_seconds=60,
            # cap on concurrent http connections per process (httpx
            # max_connections); unset uses the httpx default and excess
            # requests wait for a free connection
            max_connections=None,
            # verify the certificate chain (https only); False disables it
            tls_verify_cert=True,
            # verify the certificate hostname (https only, bool); False skips
            # the hostname check while still verifying the chain
            tls_verify_hostname=True,
            # path to a CA bundle to trust instead of the system store
            tls_ca=None,
        )

    #: (tokeo.core.pocketbase.PocketBase) The PocketBase client, set on setup
    pb = None

    def __init__(self, app, *args, **kw):
        super(TokeoPocketBaseHandler, self).__init__(*args, **kw)
        #: The Cement application instance
        self.app = app

    def _setup(self, app):
        """
        Set up the PocketBase handler.

        Initializes the PocketBase client with the configured URL and options.
        This method is called by the framework after all configuration
        has been loaded.

        ### Args

        - **app**: The Tokeo application instance

        ### Notes

        : Merges the default configuration with any user-provided configuration
            and initializes the PocketBase client, stored in the ```pb```
            attribute and used by all other methods

        : A https url enables tls, verified against the system ca store by
            default; the tls options build a custom ssl context that is
            passed to the underlying httpx client

        : ```max_connections``` caps concurrent http connections per process
            via httpx; it is not a request-rate limit and does not span
            processes, so apply any rate or cross-process throttling in your
            application (e.g. the diskcache throttle recipe)

        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        url = self._config('url')
        # tls is driven by the https scheme; the tls_* options only customize
        # it, so a plain http url ignores them
        context = None
        if url and url.lower().startswith('https'):
            context, _ = tls_create_ssl_context(
                self._config('tls_verify_hostname'),
                self._config('tls_verify_cert'),
                self._config('tls_ca'),
            )
        # build the httpx client kwargs: a custom tls context (custom ca or
        # relaxed verification) and an optional concurrency cap
        client_kwargs = {}
        if context is not None:
            client_kwargs['verify'] = context
        max_connections = self._config('max_connections')
        if max_connections:
            client_kwargs['limits'] = httpx.Limits(max_connections=max_connections)
        # anonymous until a service identity is configured; ensure_auth runs
        # before each request of the client, also of collection() and batches
        self.pb = PocketBase(url, before_request=self.ensure_auth, **client_kwargs)
        # cache the auth config once: the per-request guard must not hit
        # self._config on every call
        self._auth_identity = self._config('auth_identity')
        self._auth_min_valid = self._config('auth_token_min_valid_seconds')
        # guards lazy login and token refresh against concurrent access, since
        # the login of the shared client has no locking of its own
        self._auth_lock = threading.Lock()
        # the collection a successful login used; reused for refresh and
        # re-login, set on the first successful authentication
        self._auth_collection = None

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a simple wrapper around the application's config.get method
        that automatically uses the correct configuration section.

        ### Args

        - **key** (str): Configuration key to retrieve

        ### Keyword Args

        - **kwargs**: Additional arguments passed to config.get(), such as
            default values

        ### Returns

        - **Any**: Configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def _login(self):
        """
        Authenticate the service identity and remember the collection used.

        Must be called with the auth lock held. On a reconnect it reuses the
        remembered collection; otherwise it tries the collections in
        _AUTH_COLLECTIONS (the users collection first, then the _superusers
        admin collection), then stores whichever succeeded for later refresh
        and re-login.

        ### Raises

        - **TokeoPocketBaseAuthError**: If no candidate collection accepts the
            credentials

        """
        # reuse the remembered collection on reconnect, else run discovery
        if self._auth_collection:
            candidates = [self._auth_collection]
        else:
            candidates = _AUTH_COLLECTIONS
        last_error = None
        for name in candidates:
            try:
                self.pb.auth_with_password(name, self._auth_identity, self._config('auth_password'))
                self._auth_collection = name
                return
            except Exception as error:
                last_error = error
        raise TokeoPocketBaseAuthError(f'PocketBase authentication failed for identity {self._auth_identity!r}') from last_error

    def ensure_auth(self):
        """
        Ensure a valid auth token before a request, when credentials are set.

        Called by the client before each of its requests, also those of
        collection(), batches and send. When auth_identity and auth_password
        are configured this lazily logs in on first use and, on later calls,
        refreshes the token before it expires. With no credentials
        configured the handler stays anonymous and this is a no-op.

        ### Raises

        - **TokeoPocketBaseAuthError**: If authentication ultimately fails

        ### Notes

        : The whole check-and-refresh runs under a lock, because the login of
            the shared client is mutable state with no locking; this prevents
            concurrent workers from racing on refresh or re-login

        : A token with more than auth_token_min_valid_seconds of life left is
            used as is; one near expiry is refreshed on its collection, and a
            failed refresh falls through to a full re-login

        """
        # nothing to do unless a service identity is configured
        if not self._auth_identity:
            return
        with self._auth_lock:
            # a token with more than the minimum valid window left is good
            if self.pb.token_valid(self._auth_min_valid):
                return
            # a still-valid token near expiry can be refreshed on its collection
            if self._auth_collection and self.pb.token_valid(0):
                try:
                    self.pb.auth_refresh()
                    return
                except TokeoPocketBaseError:
                    # refresh rejected (e.g. revoked): fall back to a full login
                    pass
            self._login()

    def close(self):
        """
        Close the PocketBase connection.

        Lifecycle hook required by the database handler contract and wired
        to the pre_close hook via pocketbase_close(). It clears any stored
        auth token so the identity does not outlive the handler, and closes
        the connection pool of the http client; the handler is not usable
        afterwards.

        """
        # destroy the token so a logged-in identity does not linger, and free
        # the pooled connections of the http client
        with self._auth_lock:
            self.pb.close()
            self._auth_collection = None

    def collection(self, collection):
        """
        Get the records of a PocketBase collection.

        ### Args

        - **collection** (str): The name or id of the collection

        ### Returns

        - **Collection**: The record methods of the collection

        ### Example

        ```python
        posts = app.db.collection('posts')
        everything = posts.get_full_list(sort='title')
        ```

        ### Notes

        : The handler methods below are short forms of these methods,
            ```app.db.get_one('posts', rid)``` is
            ```app.db.collection('posts').get_one(rid)```

        """
        return self.pb.collection(collection)

    def get_one(self, collection, record_id, expand=None, fields=None, query=None):
        """
        Get one record by its id.

        ### Args

        - **collection** (str): The name or id of the collection
        - **record_id** (str): The id of the record
        - **expand** (str): The relations to expand, e.g. ```owner```
        - **fields** (str): The fields to return, e.g. ```id,title```
        - **query** (dict): Further query parameters, a key here wins

        ### Returns

        - **Record**: The record

        ### Raises

        - **TokeoPocketBaseError**: If the request fails, status 404 for a
            record not found

        ### Example

        ```python
        order = app.db.get_one('orders', order_id, expand='customer')
        print(order.title, order.expand.customer.email)
        ```

        """
        return self.collection(collection).get_one(record_id, expand, fields, query)

    def get_list(self, collection, page=1, per_page=30, filter=None, sort=None, expand=None, fields=None, skip_total=False, query=None):
        """
        Get a page of records.

        ### Args

        - **collection** (str): The name or id of the collection
        - **page** (int): The page, from 1
        - **per_page** (int): The records per page
        - **filter** (str): The filter in the syntax of PocketBase
        - **sort** (str): The sort order, e.g. ```-created,title```
        - **expand** (str): The relations to expand
        - **fields** (str): The fields to return
        - **skip_total** (bool): Skip counting all records, faster
        - **query** (dict): Further query parameters, a key here wins

        ### Returns

        - **ListResult**: The records in ```items``` and ```page```,
            ```per_page```, ```total_items``` and ```total_pages```; the totals
            are -1 with skip_total

        ### Raises

        - **TokeoPocketBaseError**: If the request fails

        ### Example

        ```python
        paid = app.db.pb.filter('status = {:s}', {'s': 'paid'})
        orders = app.db.get_list('orders', per_page=50, filter=paid)
        print(f'{len(orders.items)} of {orders.total_items} orders')
        ```

        ### Notes

        : Build a filter from outside values only by
            ```app.db.pb.filter(expr, params)```, it quotes and escapes them

        """
        return self.collection(collection).get_list(page, per_page, filter, sort, expand, fields, skip_total, query)

    def get_full_list(self, collection, per_page=500, filter=None, sort=None, expand=None, fields=None, query=None):
        """
        Get all records that match, page by page.

        ### Args

        - **collection** (str): The name or id of the collection
        - **per_page** (int): The records per request
        - **filter** (str): The filter in the syntax of PocketBase
        - **sort** (str): The sort order
        - **expand** (str): The relations to expand
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters, a key here wins

        ### Returns

        - **list**: All records that match

        ### Raises

        - **TokeoPocketBaseError**: If a request fails

        """
        return self.collection(collection).get_full_list(per_page, filter, sort, expand, fields, query)

    def get_first(self, collection, filter, sort=None, expand=None, fields=None, query=None):
        """
        Get the first record that matches a filter.

        ### Args

        - **collection** (str): The name or id of the collection
        - **filter** (str): The filter in the syntax of PocketBase
        - **sort** (str): The sort order
        - **expand** (str): The relations to expand
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters, a key here wins

        ### Returns

        - **Record**: The first record

        ### Raises

        - **TokeoPocketBaseError**: If the request fails, status 404 if no
            record matches

        """
        return self.collection(collection).get_first(filter, sort, expand, fields, query)

    def create(self, collection, data=None, files=None, expand=None, fields=None, query=None):
        """
        Create a record.

        ### Args

        - **collection** (str): The name or id of the collection
        - **data** (dict): The fields of the record
        - **files** (list): Files to upload, as ```(field, (filename,
            content, content_type))``` tuples
        - **expand** (str): The relations to expand in the answer
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters, a key here wins

        ### Returns

        - **Record**: The new record

        ### Raises

        - **TokeoPocketBaseError**: If the creation fails, the invalid fields
            in ```data```

        ### Example

        ```python
        product = app.db.create('products', {'name': 'Widget', 'price': 99.99})
        print(product.id)
        ```

        ### Notes

        : A record of an auth collection needs ```password``` and
            ```passwordConfirm``` with the same value

        """
        return self.collection(collection).create(data, files, expand, fields, query)

    def update(self, collection, record_id, data=None, files=None, expand=None, fields=None, query=None):
        """
        Change fields of a record.

        ### Args

        - **collection** (str): The name or id of the collection
        - **record_id** (str): The id of the record
        - **data** (dict): The fields to change, the others stay
        - **files** (list): Files to upload, as ```(field, (filename,
            content, content_type))``` tuples; a field ```name+``` appends
        - **expand** (str): The relations to expand in the answer
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters, a key here wins

        ### Returns

        - **Record**: The changed record

        ### Raises

        - **TokeoPocketBaseError**: If the change fails, status 404 for a
            record not found

        ### Example

        ```python
        app.db.update('orders', order_id, {'status': 'processing'})
        ```

        """
        return self.collection(collection).update(record_id, data, files, expand, fields, query)

    def delete(self, collection, record_id, query=None):
        """
        Delete a record.

        ### Args

        - **collection** (str): The name or id of the collection
        - **record_id** (str): The id of the record
        - **query** (dict): Further query parameters

        ### Returns

        - **bool**: True once deleted

        ### Raises

        - **TokeoPocketBaseError**: If the deletion fails, e.g. not found or
            not allowed

        ### Notes

        : Deletion is permanent. For sensitive data, consider a soft delete
            by a status field instead

        """
        return self.collection(collection).delete(record_id, query)


def pocketbase_extend_app(app):
    """
    Extend the application with PocketBase functionality.

    This function adds the PocketBase handler to the application and
    initializes it, making it available as app.db throughout the application.

    ### Args

    - **app**: The Tokeo application instance

    ### Notes

    : This function is called automatically during the application's post_setup
        phase. It creates an instance of TokeoPocketBaseHandler, configures it,
        and attaches it to the application as the 'db' attribute.

    """
    app.extend('db', TokeoPocketBaseHandler(app))
    app.db._setup(app)


def pocketbase_close(app):
    """
    Handle application shutdown for PocketBase.

    Pre_close hook that delegates to the handler's close() method.

    ### Args

    - **app**: The Tokeo application instance

    ### Notes

    : Called automatically during the application's pre_close phase. It
        invokes app.db.close(), which drops the login and closes the
        connections of the client.

    """
    app.db.close()


def load(app):
    """
    Load the PocketBase extension into a Tokeo application.

    This function registers the PocketBase handler and hooks needed for
    integration with the Tokeo application framework. It's called automatically
    when the extension is loaded.

    ### Args

    - **app**: The Tokeo application instance

    ### Example

    ```python
    # In your application configuration:
    class MyApp(App):
        class Meta:
            extensions = [
                'tokeo.ext.pocketbase',
                # other extensions...
            ]
    ```

    ### Notes

    : This function performs three key actions:

        - Sets the default database handler for the application
        - Registers a post_setup hook to initialize the PocketBase client
        - Registers a pre_close hook for proper cleanup

    : After loading this extension, the PocketBase client is available
        through the app.db attribute in your application code.

    """
    # Set the default database handler for the application
    app._meta.db_handler = TokeoPocketBaseHandler.Meta.label

    # Register hooks for initialization and cleanup
    app.hook.register('post_setup', pocketbase_extend_app)
    app.hook.register('pre_close', pocketbase_close)
