from cement.core import cache
from contextlib import contextmanager
import diskcache


class LockError(Exception):
    """Signal errors on locking."""

    pass


class TokeoDiskCacheCacheHandler(cache.CacheHandler):

    """
    This class implements the :ref:`Cache <cement.core.cache>` Handler
    interface.  It provides a caching interface using the
    `diskcache <https://github.com/grantjenks/python-diskcache>`_ library.
    """

    class Meta:

        """Handler meta-data."""

        #: Unique identifier for this handler
        label = 'tokeo.diskcache'

        #: Id for config
        config_section = 'diskcache'

        #: Dict with initial settings
        config_defaults = dict(
            directory=None,
            timeout=60,
            expire_time=None,
        )

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)

    def _setup(self, *args, **kw):
        super()._setup(*args, **kw)
        # create the key/value store for datas (permanent)
        self.c = diskcache.Cache(
            directory=self._config('directory'),
            timeout=self._config('timeout'),
        )

    def _config(self, key, default=None):
        """
        This is a simple wrapper, and is equivalent to:
        ``self.app.config.get('cache.redis', <key>)``.

        Args:
            key (str): The key to get a config value from the ``cache.redis``
                config section.

        Returns:
            unknown: The value of the given key.

        """
        return self.app.config.get(self._meta.config_section, key)

    def get(self, key, default=None, **kw):
        """
        Get a value from the cache.  Additional keyword arguments are ignored.

        Args:
            key (str): The key of the item in the cache to get.

        Keyword Args:
            fallback: The value to return if the item is not found in the
                cache.

        Returns:
            unknown: The value of the item in the cache, or the ``fallback``
            value.

        """
        return self.c.get(key, default)

    def set(self, key, value, **kw):
        """
        Set a value in the cache for the given ``key``.  Additional
        keyword arguments are ignored.

        Args:
            key (str): The key of the item in the cache to set.
            value: The value of the item to set.
            time (int): The expiration time (in seconds) to keep the item
                cached. Defaults to ``expire_time`` as defined in the
                applications configuration.

        """
        expire = kw.get('expire', self._config('expire_time'))

        if expire is None or expire == 0:
            return self.c.set(key, value)
        else:
            return self.c.set(key, value, expire=expire)

    def delete(self, key, **kw):
        """
        Delete an item from the cache for the given ``key``.  Additional
        keyword arguments are ignored.

        Args:
            key (str): The key to delete from the cache.

        """
        self.c.delete(key)

    def purge(self, **kw):
        """
        Purge the entire cache, all keys and values will be lost.  Any
        additional keyword arguments will be passed directly to the
        redis ``flush_all()`` function.

        """
        self.c.clear()

    ### --------------------------------------------------------------------------------------

    def add(self, key, value, expire=None):
        return self.c.add(key, value, expire=expire)

    def touch(self, key, expire):
        return self.c.touch(key, expire=expire)

    ### --------------------------------------------------------------------------------------

    def acquire(self, key, expire=None):
        lock = diskcache.Lock(self.c, key, expire=expire)
        lock.acquire()

    def release(self, key):
        lock = diskcache.Lock(self.c, key)
        lock.release()

    def locked(self, key):
        lock = diskcache.Lock(self.c, key)
        return lock.locked()

    @contextmanager
    def lock(self, key, expire=None):
        try:
            lock = diskcache.Lock(self.c, key, expire=expire)
            lock.acquire()
            yield
        finally:
            lock.release()


def load(app):
    app._meta.cache_handler = TokeoDiskCacheCacheHandler.Meta.label
    app.handler.register(TokeoDiskCacheCacheHandler)
