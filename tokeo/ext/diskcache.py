from contextlib import contextmanager
from cement.core import cache
import diskcache


class LockError(Exception):
    """Signal errors on locking."""

    pass


class TokeoDiscCacheHandler(cache.CacheHandler):

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
        super(TokeoDiscCacheHandler, self).__init__(*args, **kw)

    def _setup(self, *args, **kw):
        super(TokeoDiscCacheHandler, self)._setup(*args, **kw)
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

    def add(self, key, value, expire=None):
        return self.c.add(key, value, expire=expire)

    def touch(self, key, expire):
        return self.c.touch(key, expire=expire)

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

    def locker(self, key_prefix=None, key=None, key_append=None, expire=None, tag=None, cb_on_locked=None):
        """
        This is a lock decorator taken the key from the wrapped function name
        """

        def decorator(func):

            def wrapper(*args, **kwargs):

                # create key from @decorated function name
                _key = func.__name__ if key is None or key == '' else key
                # prepend some prefix if set
                _key = key_prefix + '_' + _key if key_prefix is not None and key_prefix != '' else _key
                # append some appendix if set
                if key_append is not None:
                    if isinstance(key_append, str) and key_append != '':
                        # just add some string
                        _key = _key + '_' + key_append
                    elif isinstance(key_append, list) and key_append:
                        for _key_append in key_append:
                            # when list then test to evaluate for args and kwargs
                            if _key_append in args:
                                _key = _key + '_' + args[_key_append]
                            elif _key_append in kwargs:
                                _key = _key + '_' + kwargs[_key_append]
                            else:
                                # when not in args or kwargs then add just as string
                                _key = _key + '_' + _key_append
                # info
                self.app.log.info(f'Handle {func.__name__} inside @locker using key {_key}')
                # create the lock object
                lock = diskcache.Lock(self.c, _key.strip(), expire=expire, tag=tag)
                # return immediately if not wait_on_locked
                if cb_on_locked is not None and lock.locked():
                    # debug info
                    self.app.log.debug(f'Locked key {_key}! Will call cb func')
                    # callback
                    cb_on_locked()

                # debug info
                self.app.log.debug(f'Waiting {func.__name__} for unlocked key {_key}')
                # acquire the lock
                with lock:
                    # debug info
                    self.app.log.debug(f'Call {func.__name__} with locked key {_key}')
                    # call the wrapped function
                    result = func(*args, **kwargs)
                    # returning the value to the original frame
                    return result

            # call the wrapper and set it's name to the wrapped function
            wrapper.__name__ = func.__name__
            return wrapper

        # call the decorator
        return decorator


def load(app):
    app._meta.cache_handler = TokeoDiscCacheHandler.Meta.label
    app.handler.register(TokeoDiscCacheHandler)
