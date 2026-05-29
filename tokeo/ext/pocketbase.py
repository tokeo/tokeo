"""
Tokeo PocketBase Extension Module.

This extension integrates PocketBase with Tokeo applications, providing a
lightweight database solution and backend services. PocketBase is a
zero-config backend built on SQLite that provides REST API, authentication,
and realtime subscriptions.

The extension exposes a Cement-style database handler, reachable as app.db
throughout your Tokeo application, that maps the usual CRUD methods onto
PocketBase collections. Operations beyond that surface (such as auth flows)
are reached through the raw SDK via the collection() escape hatch.

### Features

- Seamless integration with Tokeo applications
- Simple CRUD operations for PocketBase collections
- Advanced querying, filtering, and sorting on lists
- User and auth collections handled like any other via CRUD
- Direct SDK access through the collection() escape hatch for anything
  beyond the CRUD surface (auth flows, file fields, schema, etc.)

### Example

```python
    # Get data from a collection with filtering and sorting
    records = app.db.get_list(
        'products',
        filter='price > 100',
        sort='-created,name',
        perPage=50
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

from cement.core.meta import MetaMixin
import pocketbase


class TokeoPocketBaseHandler(MetaMixin):
    """
    PocketBase integration handler for Tokeo applications.

    A Cement-style database handler that maps the usual CRUD methods
    (get_one, get_list, create, update, delete) onto PocketBase
    collections. For anything outside that surface, collection() hands
    back the raw SDK collection object.

    ### Notes

    - The handler is registered as 'db' and is reached through app.db. It
      is a thin convenience layer over the PocketBase SDK, not a wrapper
      around its full surface.

    - Authentication: the handler connects anonymously, so the mapped CRUD
      calls run against whatever the PocketBase api rules permit for
      unauthenticated requests. Auth and user management happen at the
      application level - user records via plain CRUD on the auth
      collection, login/token flows via collection().auth_with_password()
      and friends. There is intentionally no handler-level login.

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
            # URL for the PocketBase server
            url='http://127.0.0.1:8090',
        )

    def __init__(self, app, *args, **kw):
        """
        Initialize the PocketBase handler.

        Sets up the handler and prepares it for use with the Tokeo application.
        The actual PocketBase client initialization happens in the _setup method.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Variable length argument list passed to parent initializer
        - ****kw**: Arbitrary keyword arguments passed to parent initializer

        ### Notes

        : This constructor only stores the application reference. The actual
            PocketBase client initialization is deferred until the _setup method
            is called by the framework to ensure proper configuration loading.

        """
        super(TokeoPocketBaseHandler, self).__init__(*args, **kw)
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

        : This method merges the default configuration with any user-provided
            configuration and initializes the PocketBase client. The client is
            stored in the `pb` attribute and is used by all other methods to
            interact with the PocketBase server.

        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        # connect anonymously by design: this handler is a thin CRUD mapping
        # and runs against whatever the PocketBase api rules allow for
        # unauthenticated requests; auth flows are the app's job via collection()
        self.pb = pocketbase.PocketBase(self._config('url'))

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a simple wrapper around the application's config.get method
        that automatically uses the correct configuration section.

        ### Args

        - **key** (str): Configuration key to retrieve
        - **kwargs**: Additional arguments passed to config.get(), such as
            default values

        ### Returns

        - **Any**: Configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def close(self):
        """
        Close the PocketBase connection.

        Lifecycle hook required by the database handler contract and wired
        to the pre_close hook via pocketbase_close(). The handler connects
        anonymously and holds no session or pooled connection, so there is
        nothing to tear down and this method is a deliberate no-op.

        """
        pass

    def collection(self, collection_id_or_name):
        """
        Get a reference to a PocketBase collection.

        Retrieves a collection object that can be used to perform operations
        directly with the PocketBase SDK. This provides access to all
        native SDK functionality not specifically wrapped by the handler's methods.

        ### Args

        - **collection_id_or_name** (str): The ID or name of the collection

        ### Returns

        - **Collection**: A PocketBase collection object, provides direct SDK access

        ### Example

        ```python
        # Get direct access to a collection
        users_collection = app.db.collection('users')

        # Use native SDK methods
        user = users_collection.auth_with_password('email@example.com', 'password')

        # Access collection schema information
        schema = users_collection.get_schema()
        ```

        ### Notes

        : While most common operations are available through the handler's
            higher-level methods, this method provides access to the underlying
            SDK for more specialized or advanced use cases.

        """
        # return a general collection
        return self.pb.collection(collection_id_or_name)

    def get_one(self, collection_id_or_name, id_, sort=None, cache=True, q=None):
        """
        Retrieve a single record from a collection by ID.

        Fetches a record by its unique ID from the specified collection,
        with options for caching, sorting, and additional query parameters.

        ### Args

        - **collection_id_or_name** (str): The ID or name of the collection
        - **id_** (str): The ID of the record to retrieve
        - **sort** (str, optional): Sort expression for the query
            (format: `field,-field`)
        - **cache** (bool): Whether to use cached results if available
        - **q** (dict): Additional query parameters to pass to PocketBase

        ### Returns

        - **Record**: The requested record data with all its fields

        ### Raises

        - **Exception**: If record retrieval fails (e.g., record not found)

        ### Example

        ```python
        # Get a single user record
        user = app.db.get_one('users', 'abc123xyz')

        # Disable caching for fresh data
        latest_post = app.db.get_one('posts', post_id, cache=False)
        ```

        """
        # update immutable default arguments
        q = dict() if q is None else q
        # check query options
        cache_opt = dict() if cache else dict(cache='no-cache')
        sort_opt = dict() if sort is None or sort == '' else dict(sort=sort)
        # run database query for one element by id
        return self.collection(collection_id_or_name).get_one(id_, dict(**cache_opt, **sort_opt, **q))

    def get_list(self, collection_id_or_name, page=1, perPage=20, filter='', sort=None, cache=True, q=None):
        """
        Retrieve a paginated list of records from a collection.

        Fetches multiple records from the specified collection with support for
        pagination, filtering, sorting, and additional query parameters.

        ### Args

        - **collection_id_or_name** (str): The ID or name of the collection
        - **page** (int): The page number to retrieve (1-based)
        - **perPage** (int): Number of records per page (default: 20)
        - **filter** (str): Filter expression to apply (PocketBase filter syntax)
        - **sort** (str, optional): Sort expression (format: `field,-field`)
        - **cache** (bool): Whether to use cached results if available
        - **q** (dict): Additional query parameters to pass to PocketBase

        ### Returns

        - **ResultList**: Paginated result object with the following attributes:

            - **items**: List of record objects
            - **page**: Current page number
            - **perPage**: Number of items per page
            - **totalItems**: Total number of items across all pages
            - **totalPages**: Total number of pages

        ### Raises

        - **Exception**: If the query fails

        ### Example

        ```python
        # Get a basic list of users
        users = app.db.get_list('users', page=1, perPage=50)

        # Get filtered and sorted list
        posts = app.db.get_list(
            'posts',
            filter='created >= "2023-01-01" && status = "published"',
            sort='-created,title',
            perPage=100
        )

        # Access the results
        print(f"Showing {len(posts.items)} of {posts.totalItems} posts")
        for post in posts.items:
            print(f"Post: {post.title} by {post.author}")

        # Check if there are more pages
        if posts.page < posts.totalPages:
            # Get next page
            next_page = app.db.get_list('posts', page=posts.page+1)
        ```

        ### Notes

        : The filter parameter uses PocketBase's filter syntax, which is similar to
            JavaScript expressions. You can use logical operators (&&, ||),
            comparison operators (=, !=, >, <, >=, <=), and
            various filter functions.

        """
        # update immutable default arguments
        q = dict() if q is None else q
        # check query options
        cache_opt = dict() if cache else dict(cache='no-cache')
        sort_opt = dict() if sort is None or sort == '' else dict(sort=sort)
        # run database query for multiple elements
        return self.collection(collection_id_or_name).get_list(page, perPage, dict(filter=filter, **cache_opt, **sort_opt, **q))

    def create(self, collection_id_or_name, create_fields=None, q=None):
        """
        Create a new record in a collection.

        Creates a new record with the specified fields in the given collection.

        ### Args

        - **collection_id_or_name** (str): The ID or name of the collection
        - **create_fields** (dict): The fields for the new record
        - **q** (dict): Additional query parameters to pass to PocketBase

        ### Returns

        - **Record**: The newly created record with all its fields

        ### Raises

        - **Exception**: If record creation fails

        ### Example

        ```python
        # Create a basic record
        new_product = app.db.create('products', {
            'name': 'Premium Widget',
            'price': 99.99,
            'description': 'High-quality widget with premium features',
            'in_stock': True
        })

        # Use the returned record
        print(f"Created record with ID: {new_product.id}")
        ```

        ### Notes

        : When creating users or other records with passwords, be sure to include
            both the `password` and `passwordConfirm` fields with identical values
            as required by PocketBase.

        """
        # update immutable default arguments
        create_fields = dict() if create_fields is None else create_fields
        q = dict() if q is None else q
        # run database create
        return self.collection(collection_id_or_name).create(body_params=create_fields, query_params=q)

    def update(self, collection_id_or_name, id_, update_fields=None, q=None):
        """
        Update an existing record in a collection.

        Updates an existing record with new field values. Only specified fields
        will be updated; others remain unchanged.

        ### Args

        - **collection_id_or_name** (str): The ID or name of the collection
        - **id_** (str): The ID of the record to update
        - **update_fields** (dict): The fields to update
        - **q** (dict): Additional query parameters to pass to PocketBase

        ### Returns

        - **Record**: The updated record with all its fields

        ### Raises

        - **Exception**: If record update fails (e.g., record not found)

        ### Example

        ```python
        # Update basic fields
        updated_product = app.db.update('products', 'record123', {
            'price': 129.99,
            'in_stock': False,
            'discount': 0.15
        })

        # Partial update of a user record
        app.db.update('users', user_id, {
            'is_verified': True
        })
        ```

        ### Notes

        : Updates are partial by default, meaning only the fields specified in
            update_fields will be modified. To remove a field value, set it to null
            explicitly in the update_fields dictionary.

        """
        # update immutable default arguments
        update_fields = dict() if update_fields is None else update_fields
        q = dict() if q is None else q
        # run database update
        return self.collection(collection_id_or_name).update(id_, body_params=update_fields, query_params=q)

    def delete(self, collection_id_or_name, id_, q=None):
        """
        Delete a record from a collection.

        Permanently removes a record from the specified collection.

        ### Args

        - **collection_id_or_name** (str): The ID or name of the collection
        - **id_** (str): The ID of the record to delete
        - **q** (dict): Additional query parameters to pass to PocketBase

        ### Returns

        - **bool**: True if the deletion was successful

        ### Raises

        - **Exception**: If record deletion fails (e.g., record not found
            or unauthorized)

        ### Example

        ```python
        try:
            # Delete a record
            result = app.db.delete('products', 'record123')
            if result:
                print("Record successfully deleted")
        except Exception as e:
            print(f"Failed to delete record: {str(e)}")
        ```

        ### Notes

        : Deletion is permanent and cannot be undone. For sensitive data, consider
            implementing soft deletes by updating a status field instead of
            permanently deleting records.

        """
        # update immutable default arguments
        q = dict() if q is None else q
        # run database delete
        return self.collection(collection_id_or_name).delete(id_, query_params=q)


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
        invokes app.db.close(), which is a deliberate no-op (the handler
        keeps no session or connection); see close() for the rationale.

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
