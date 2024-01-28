from cement.core.meta import MetaMixin
import pocketbase


class TokeoPocketBaseHandler(MetaMixin):

    class Meta:

        """Handler meta-data."""

        #: Unique identifier for this handler
        label = 'tokeo.pocketbase'

        #: Id for config
        config_section = 'pocketbase'

        #: Dict with initial settings
        config_defaults = dict(
            url='http://127.0.0.1:8090',
        )

    def __init__(self, app, *args, **kw):
        super(TokeoPocketBaseHandler, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        self.p = pocketbase.PocketBase(self._config('url'))
        # [ ] TODO: Authentication is missing

    def _config(self, key, default=None):
        """
        This is a simple wrapper, and is equivalent to: ``self.app.config.get(<section>, <key>)``.
        """
        return self.app.config.get(self._meta.config_section, key)

    def close(self):
        pass

    def collection(self, collection_id_or_name):
        # return a general collection
        return self.p.collection(collection_id_or_name)

    def get_one(self, collection_id_or_name, id, sort=None, cache=True, q=dict()):
        # check query options
        cache_opt = dict() if cache else dict(cache='no-cache')
        sort_opt = dict() if sort is None or sort == '' else dict(sort=sort)
        # run database query for one element by id
        return self.collection(collection_id_or_name).get_one(id, dict(**cache_opt, **sort_opt, **q))

    def get_list(self, collection_id_or_name, page=1, perPage=20, filter='', sort=None, cache=True, q=dict()):
        # check query options
        cache_opt = dict() if cache else dict(cache='no-cache')
        sort_opt = dict() if sort is None or sort == '' else dict(sort=sort)
        # run database query for multiple elements
        return self.collection(collection_id_or_name).get_list(page, perPage, dict(filter=filter, **cache_opt, **sort_opt, **q))

    def create(self, collection_id_or_name, create_fields=dict(), q=dict()):
        # run database create
        return self.collection(collection_id_or_name).create(body_params=create_fields, query_params=q)

    def update(self, collection_id_or_name, id, update_fields=dict(), q=dict()):
        # run database update
        return self.collection(collection_id_or_name).update(id, body_params=update_fields, query_params=q)

    def delete(self, collection_id_or_name, id, q=dict()):
        # run database delete
        return self.collection(collection_id_or_name).delete(id, query_params=q)


def pocketbase_extend_app(app):
    app.extend('db', TokeoPocketBaseHandler(app))
    app.db._setup(app)

def pocketbase_close(app):
    app.db.close()

def load(app):
    app._meta.db_handler = TokeoPocketBaseHandler.Meta.label
    app.hook.register('post_setup', pocketbase_extend_app)
    app.hook.register('pre_close', pocketbase_close)
