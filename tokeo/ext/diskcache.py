from cement.core import cache
import inspect
from contextlib import contextmanager
import functools
import diskcache
import time


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
            directory=self._config('data_cache_directory'),
            timeout=self._config('timeout'),
        )
        # create a key/value store for locks (purge on start)
        self.l = diskcache.Cache(
            directory=self._config('lock_cache_directory'),
        )
        # purge on init
        self.l.clear()

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
        lock = diskcache.Lock(self.l, key, expire=expire)
        lock.acquire()

    def release(self, key):
        lock = diskcache.Lock(self.l, key)
        lock.release()

    def locked(self, key):
        lock = diskcache.Lock(self.l, key)
        return lock.locked()

    @contextmanager
    def lock(self, key, expire=None):
        try:
            lock = diskcache.Lock(self.l, key, expire=expire)
            lock.acquire()
            yield
        finally:
            lock.release()

    ### --------------------------------------------------------------------------------------

    def throttle(
        self,
        count,
        per_seconds,
        name=None,
        name_f=None,
        expire=None,
        tag=None,
        time_func=time.time,
        sleep_func=time.sleep,
        cb_on_locked=None,
        purge_on_exit=True,
    ):
        """

        Decorator to throttle calls to function.

        """

        def decorator(func):
            # create the full name of the @decorated function
            func_full_name = diskcache.core.full_name(func)
            # calc the rate
            rate = count / float(per_seconds)

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                # flag for function call to use
                use_cb = False
                # unpack arguments in dictionary
                arguments = dict(
                    func_name = func.__name__,
                    func_full_name = func_full_name,
                    **unpack_func_args(func, *args),
                    **kwargs,
                )
                # create key from @decorated function name or formatted string
                if isinstance(name, str) and name != '':
                    # just use the name
                    key = name
                elif isinstance(name_f, str) and name_f != '':
                    # expand the string in name_f with dict from args
                    key = name_f.format(**arguments)
                else:
                    # use full_name as key
                    key = func_full_name

                # some outputr info
                self.app.log.info(f'@throttle {func.__name__} using key {key}')

                # run in a transaction
                with self.l.transact(retry=True):
                    # check if already initialized
                    if self.l.get(key) is None:
                        # initialize the timer
                        now = time_func()
                        # set the cache
                        self.l.set(key, (now, count), expire=expire, tag=tag, retry=True)

                # loop
                while True:
                    # run in a transaction
                    with self.l.transact(retry=True):
                        last, tally = self.l.get(key)
                        now = time_func()
                        tally += (now - last) * rate
                        delay = 0

                        if tally > count:
                            self.l.set(key, (now, count - 1), expire)
                        elif tally >= 1:
                            self.l.set(key, (now, tally - 1), expire)
                        else:
                            delay = (1 - tally) / rate

                    if delay:
                        # with callback break and call callback outside the transaction
                        if cb_on_locked:
                            use_cb = True
                            break
                        # without callback stay here and sleep
                        else:
                            sleep_func(delay)
                    else:
                        # break and call func
                        break

                # call the wrapped function or callback if set
                result = func(*args, **kwargs) if not use_cb else cb_on_locked(**arguments)

                # return the @decorated result
                return result

            return wrapper

        return decorator

    ### --------------------------------------------------------------------------------------

    def temper(
        self,
        count=1,
        name=None,
        name_f=None,
        expire=None,
        tag=None,
        sleep_func=time.sleep,
        cb_on_locked=None,
        purge_on_exit=True,
    ):
        """

        Decorator to temper calls to function.

        """

        def decorator(func):
            # create the full name of the @decorated function
            func_full_name = diskcache.core.full_name(func)

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                # flag for function call to use
                use_cb = False
                # unpack arguments in dictionary
                arguments = dict(
                    func_name = func.__name__,
                    func_full_name = func_full_name,
                    **unpack_func_args(func, *args),
                    **kwargs,
                )
                # create key from @decorated function name or formatted string
                if isinstance(name, str) and name != '':
                    # just use the name
                    key = name
                elif isinstance(name_f, str) and name_f != '':
                    # expand the string in name_f with dict from args
                    key = name_f.format(**arguments)
                else:
                    # use full_name as key
                    key = func_full_name

                # some outputr info
                self.app.log.info(f'@temper {func.__name__} using key {key}')

                # run in a transaction
                with self.l.transact(retry=True):
                    # check if already initialized
                    if self.l.get(key) is None:
                        # set the cache
                        self.l.set(key, count, expire=expire, tag=tag, retry=True)

                # loop
                while True:
                    # run in a transaction
                    with self.l.transact(retry=True):
                        available = self.l.get(key)
                        delay = 0

                        if available >= 1:
                            self.l.set(key, available - 1, expire)
                        else:
                            delay = 0.05

                    if delay:
                        # with callback break and call callback outside the transaction
                        if cb_on_locked:
                            use_cb = True
                            break
                        # without callback stay here and sleep
                        else:
                            sleep_func(delay)
                    else:
                        # break and call func
                        break

                # call the wrapped function or callback if set
                result = func(*args, **kwargs) if not use_cb else cb_on_locked(**arguments)

                # run in a transaction
                with self.l.transact(retry=True):
                    # add to counter
                    self.l.set(key, self.l.get(key) + 1, expire)

                # return the @decorated result
                return result

            return wrapper

        return decorator


def load(app):
    app._meta.cache_handler = TokeoDiskCacheCacheHandler.Meta.label
    app.handler.register(TokeoDiskCacheCacheHandler)


def unpack_func_args(func, *args):
    # create a dict for return
    d = dict()
    # init loop var
    n = 0
    # get paramters from inspect
    for p in inspect.signature(func).parameters:
        d[p] = args[n]
        n += 1
    # return
    return d
