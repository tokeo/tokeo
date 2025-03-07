"""
Tokeo PocketBase Extension Module.

This extension integrates PocketBase with Tokeo applications, providing a
lightweight database solution and backend services. PocketBase is a
zero-config backend built on SQLite that provides REST API, authentication,
and realtime subscriptions.

The extension exposes PocketBase's functionality through a simple interface that
can be accessed via app.db throughout your Tokeo application.

Example:
    To use this extension in your application:

    .. code-block:: python

        from tokeo.app import TokeoApp

        with TokeoApp('myapp', extensions=['tokeo.ext.pocketbase']) as app:
            # Configure PocketBase URL in app config or use default
            # app.config.set('pocketbase', 'url', 'http://localhost:8090')

            # Get data from a collection
            records = app.db.get_list('users', perPage=50)

            # Create a new record
            new_user = app.db.create('users', {
                'username': 'example',
                'email': 'user@example.com'
            })
"""

from cement.core.meta import MetaMixin
import pocketbase


class TokeoPocketBaseHandler(MetaMixin):
    """
    PocketBase integration handler for Tokeo applications.

    This class provides a simple interface to PocketBase's functionality,
    allowing Tokeo applications to interact with PocketBase collections
    for data storage and retrieval.

    The handler is registered as 'db' in the application and can be
    accessed through app.db in your application code.
    """

    class Meta:
        """
        Handler meta-data configuration.

        Attributes:
            label (str): Unique identifier for this handler.
            config_section (str): Configuration section identifier.
            config_defaults (dict): Default configuration values.
        """

        # Unique identifier for this handler
        label = 'tokeo.pocketbase'

        # Id for config
        config_section = 'pocketbase'

        # Dict with initial settings
        config_defaults = dict(
            url='http://127.0.0.1:8090',
        )

    def __init__(self, app, *args, **kw):
        """
        Initialize the PocketBase handler.

        Args:
            app: The application object.
            *args: Variable length argument list.
            **kw: Arbitrary keyword arguments.
        """
        super(TokeoPocketBaseHandler, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        """
        Setup the PocketBase handler.

        Initializes the PocketBase client with the configured URL.

        Args:
            app: The application object.
        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        self.pb = pocketbase.PocketBase(self._config('url'))
        # [ ] TODO: Authentication is missing

    def _config(self, key, **kwargs):
        """
        Get configuration value from the handler's config section.

        This is a simple wrapper around the application's config.get method.

        Args:
            key (str): Configuration key to retrieve.
            **kwargs: Additional arguments passed to config.get().

        Returns:
            The configuration value for the specified key.
        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def close(self):
        """
        Close the PocketBase connection.

        Performs any necessary cleanup when the application is shutting down.
        """
        pass

    def collection(self, collection_id_or_name):
        """
        Get a reference to a PocketBase collection.

        Args:
            collection_id_or_name (str): The ID or name of the collection.

        Returns:
            A PocketBase collection object.
        """
        # return a general collection
        return self.pb.collection(collection_id_or_name)

    def get_one(self, collection_id_or_name, id, sort=None, cache=True, q=dict()):
        """
        Retrieve a single record from a collection by ID.

        Args:
            collection_id_or_name (str): The ID or name of the collection.
            id (str): The ID of the record to retrieve.
            sort (str, optional): Sort expression for the query.
            cache (bool): Whether to use cached results if available.
            q (dict): Additional query parameters to pass to PocketBase.

        Returns:
            The requested record data.
        """
        # check query options
        cache_opt = dict() if cache else dict(cache='no-cache')
        sort_opt = dict() if sort is None or sort == '' else dict(sort=sort)
        # run database query for one element by id
        return self.collection(collection_id_or_name).get_one(id, dict(**cache_opt, **sort_opt, **q))

    def get_list(self, collection_id_or_name, page=1, perPage=20, filter='', sort=None, cache=True, q=dict()):
        """
        Retrieve a list of records from a collection with pagination.

        Args:
            collection_id_or_name (str): The ID or name of the collection.
            page (int): The page number to retrieve (1-based).
            perPage (int): Number of records per page.
            filter (str): Filter expression to apply.
            sort (str, optional): Sort expression for the query.
            cache (bool): Whether to use cached results if available.
            q (dict): Additional query parameters to pass to PocketBase.

        Returns:
            A paginated list of records from the collection.
        """
        # check query options
        cache_opt = dict() if cache else dict(cache='no-cache')
        sort_opt = dict() if sort is None or sort == '' else dict(sort=sort)
        # run database query for multiple elements
        return self.collection(collection_id_or_name).get_list(page, perPage, dict(filter=filter, **cache_opt, **sort_opt, **q))

    def create(self, collection_id_or_name, create_fields=dict(), q=dict()):
        """
        Create a new record in a collection.

        Args:
            collection_id_or_name (str): The ID or name of the collection.
            create_fields (dict): The fields for the new record.
            q (dict): Additional query parameters to pass to PocketBase.

        Returns:
            The created record data.
        """
        # run database create
        return self.collection(collection_id_or_name).create(body_params=create_fields, query_params=q)

    def update(self, collection_id_or_name, id, update_fields=dict(), q=dict()):
        """
        Update an existing record in a collection.

        Args:
            collection_id_or_name (str): The ID or name of the collection.
            id (str): The ID of the record to update.
            update_fields (dict): The fields to update.
            q (dict): Additional query parameters to pass to PocketBase.

        Returns:
            The updated record data.
        """
        # run database update
        return self.collection(collection_id_or_name).update(id, body_params=update_fields, query_params=q)

    def delete(self, collection_id_or_name, id, q=dict()):
        """
        Delete a record from a collection.

        Args:
            collection_id_or_name (str): The ID or name of the collection.
            id (str): The ID of the record to delete.
            q (dict): Additional query parameters to pass to PocketBase.

        Returns:
            True if the deletion was successful.
        """
        # run database delete
        return self.collection(collection_id_or_name).delete(id, query_params=q)


def pocketbase_extend_app(app):
    """
    Extend the application with PocketBase functionality.

    This function adds the PocketBase handler to the application and
    initializes it, making it available as app.db.

    Args:
        app: The application object.
    """
    app.extend('db', TokeoPocketBaseHandler(app))
    app.db._setup(app)


def pocketbase_close(app):
    """
    Handle application shutdown for PocketBase.

    Properly cleans up PocketBase resources when the application is shutting down.

    Args:
        app: The application object.
    """
    app.db.close()


def load(app):
    """
    Load the PocketBase extension into a Tokeo application.

    Registers the PocketBase handler and hooks needed for integration.

    Args:
        app: The application object.
    """
    app._meta.db_handler = TokeoPocketBaseHandler.Meta.label
    app.hook.register('post_setup', pocketbase_extend_app)
    app.hook.register('pre_close', pocketbase_close)
