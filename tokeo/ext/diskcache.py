import sys
from os.path import basename, dirname, abspath
from tokeo.ext.argparse import Controller
from cement import ex
from cement.core import cache
from contextlib import contextmanager
import inspect
import functools
import diskcache
import time
import re


class LockError(Exception):
    """Signal errors on locking."""

    pass


class TokeoDiskCacheLocksHandler():

    def __init__(self, app, cache, tag, key_prefix):
        self.app = app
        self._cache = cache
        self._tag = tag
        self._key_prefix = key_prefix

    ### --------------------------------------------------------------------------------------

    def purge(self):
        total = 0
        while True:
            num = self._cache.evict(self._tag, retry=True)
            if num > 0:
                total += num
            else:
                break

        return total

    def delete(self, key, **kw):
        return self._cache.delete(self._key_prefix + key, retry=True)

    def acquire(self, key, expire=None):
        lock = diskcache.Lock(self._cache, self._key_prefix + key, expire=expire, tag=self._tag)
        return lock.acquire()

    def release(self, key):
        lock = diskcache.Lock(self._cache, self._key_prefix + key, tag=self._tag)
        lock.release()

    def locked(self, key):
        lock = diskcache.Lock(self._cache, self._key_prefix + key, tag=self._tag)
        return lock.locked()

    @contextmanager
    def lock(self, key, expire=None):
        try:
            lock = diskcache.Lock(self._cache, self._key_prefix + key, expire=expire, tag=self._tag)
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
        time_func=time.time,
        sleep_func=time.sleep,
        cb_on_locked=None,
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
                    key = self._key_prefix + name
                elif isinstance(name_f, str) and name_f != '':
                    # expand the string in name_f with dict from args
                    key = self._key_prefix + name_f.format(**arguments)
                else:
                    # use full_name as key
                    key = self._key_prefix + func_full_name

                # some outputr info
                self.app.log.info(f'@throttle {func.__name__} using key {key}')

                # run in a transaction
                with self._cache.transact(retry=True):
                    # check if already initialized
                    if self._cache.get(key) is None:
                        # initialize the timer
                        now = time_func()
                        # set the cache
                        self._cache.set(key, (now, count), tag=self._tag)

                # loop
                while True:
                    # run in a transaction
                    with self._cache.transact(retry=True):
                        last, tally = self._cache.get(key)
                        now = time_func()
                        tally += (now - last) * rate
                        delay = 0

                        if tally > count:
                            self._cache.set(key, (now, count - 1), tag=self._tag)
                        elif tally >= 1:
                            self._cache.set(key, (now, tally - 1), tag=self._tag)
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
        sleep_func=time.sleep,
        cb_on_locked=None,
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
                    key = self._key_prefix + name
                elif isinstance(name_f, str) and name_f != '':
                    # expand the string in name_f with dict from args
                    key = self._key_prefix + name_f.format(**arguments)
                else:
                    # use full_name as key
                    key = self._key_prefix + func_full_name

                # some outputr info
                self.app.log.info(f'@temper {func.__name__} using key {key}')

                # run in a transaction
                with self._cache.transact(retry=True):
                    # check if already initialized
                    if self._cache.get(key) is None:
                        # set the cache
                        self._cache.set(key, count, tag=self._tag)

                # loop
                while True:
                    # run in a transaction
                    with self._cache.transact(retry=True):
                        available = self._cache.get(key)
                        delay = 0

                        if available >= 1:
                            self._cache.set(key, available - 1, tag=self._tag)
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
                with self._cache.transact(retry=True):
                    # add to counter
                    self._cache.set(key, self._cache.get(key) + 1, tag=self._tag)

                # return the @decorated result
                return result

            return wrapper

        return decorator

    ### --------------------------------------------------------------------------------------


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
            locks_tag='diskcache_locks',
            locks_key_prefix='dc_',
        )

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._cache = None

    def _setup(self, *args, **kw):
        super()._setup(*args, **kw)
        # create the key/value store for datas (permanent)
        self._cache = diskcache.Cache(
            directory=self._config('directory'),
            timeout=self._config('timeout'),
        )
        # create a locks handler for cache
        self.locks = self.locks_handler(
            self._config('locks_tag'),
            self._config('locks_key_prefix'),
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

    def locks_handler(self, tag, locks_key_prefix):
        return TokeoDiskCacheLocksHandler(self.app, self._cache, tag, locks_key_prefix)

    def get(self, key, default=None, **kw):
        """
        Get a value from the cache.

        Args:
            key (str): The key of the item in the cache to get.

        Keyword Args:
            fallback: The value to return if the item is not found in the
                cache.

        Returns:
            unknown: The value of the item in the cache, or the ``fallback``
            value.

        """
        read = kw.get('read', False)
        retry = kw.get('retry', False)
        return self._cache.get(key, default, read=read, retry=retry)

    def set(self, key, value, **kw):
        """
        Set a value in the cache for the given ``key``.

        Args:
            key (str): The key of the item in the cache to set.
            value: The value of the item to set.
            time (int): The expiration time (in float seconds) to keep
                the item cached.

        """

        expire = kw.get('expire', None)
        tag = kw.get('tag', None)
        read = kw.get('read', False)
        retry = kw.get('retry', False)
        return self._cache.set(key, value, expire=expire, tag=tag, read=read, retry=retry)

    def delete(self, key, **kw):
        """
        Delete an item from the cache for the given ``key``.  Additional
        keyword arguments are ignored.

        Args:
            key (str): The key to delete from the cache.

        """
        retry = kw.get('retry', False)
        return self._cache.delete(key, retry=retry)

    def purge(self, **kw):
        """
        Purge the entire cache, all keys and values will be lost.  Any
        additional keyword arguments will be passed directly to the
        redis ``flush_all()`` function.

        """
        retry = kw.get('retry', False)
        return self._cache.clear(retry=retry)

    ### --------------------------------------------------------------------------------------

    def check(self, fix=False, retry=False):
        return self._cache.check(fix=fix, retry=retry)

    # create an alias for purge to original
    clear = purge

    def evict(self, tag, retry=False):
        total = 0
        while True:
            num = self._cache.evict(tag, retry=True)
            if num > 0:
                total += num
            else:
                break

        return total

    def expire(self, now=None, retry=False):
        total = 0
        while True:
            num = self._cache.expire(now=now, retry=retry)
            if num > 0:
                total += num
            else:
                break

        return total

    def add(self, key, value, expire=None, tag=None, read=False, retry=False):
        return self._cache.add(key, value, expire=expire, tag=tag, read=read, retry=retry)

    def touch(self, key, expire=None, retry=False):
        return self._cache.touch(key, expire=expire, retry=False)

    def transact(self, retry=False):
        return self._cache.transact(retry=retry)

    def stats(self, reset=False):
        return self._cache.stats(reset=reset)

    def volume(self):
        return self._cache.volume()

class TokeoDiskCacheController(Controller):
    """

    A Cement controller for managing the diskcache content.


    """

    class Meta:
        """
        Meta configuration for the DiskCache controller.

        Defines the label, type, parent, parser options, help text, description, and
        epilog for the controller.

        """

        label = 'cache'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'manage the cache content'
        description = 'Provides command-line interfaces to manage diskcache content.'
        epilog = f'Example: {basename(sys.argv[0])} cache command --options'


    @ex(
        help='verify the cache',
        description='Maintain and verify the diskcache cache.',
        epilog=f'Use "{basename(sys.argv[0])} cache check" to drop expired items and check the cache.',
    )
    def check(self):
        directory = self.app.cache._cache.directory
        print(f'Check cache at "{directory}".')
        vol = self.app.cache.volume()
        print(f'Used {vol} bytes on volume.')
        result = self.app.cache.check(fix=True, retry=True)
        print(f'Results from cache fix:\n    {result}')
        num = self.app.cache.expire(retry=True)
        print(f'Removed {num} expired items from cache.')
        cnt = self.app.cache._cache.__len__()
        print(f'Currently {cnt} keys in cache.')


    @ex(
        help='list the current cache content',
        description='Show and filter the current cache content from diskcache.',
        epilog=f'Use "{basename(sys.argv[0])} cache list" with options like "--with-values" to show the content from cache.',
        arguments=[
            (
                ['keys'],
                dict(
                    action='store',
                    nargs='*',
                    help='list only for keys like regular expressions',
                ),
            ),
            (
                ['--tag'],
                dict(
                    action='store',
                    help='show keys for tag only',
                ),
            ),
            (
                ['--with-values'],
                dict(
                    action='store_true',
                    help='show current values for keys',
                ),
            ),
            (
                ['--with-expires'],
                dict(
                    action='store_true',
                    help='show expire time for keys',
                ),
            ),
            (
                ['--with-tags'],
                dict(
                    action='store_true',
                    help='show tags for keys',
                ),
            ),
        ],
    )
    def list(self):
        # iter over all keys stored in cache
        for key in self.app.cache._cache.iterkeys():
            # check for keys parameter and try to match regex
            if self.app.pargs.keys:
                _show = False
                for s_key in self.app.pargs.keys:
                    if re.search(s_key, key):
                        _show = True
                        break
            # show entry
            else:
                _show = True
            # check additional params and informations
            if _show:
                out = '{key}'
                if self.app.pargs.with_values:
                    out += ' = {value}'
                if self.app.pargs.with_expires:
                    out += ' ({expire_time})s'
                if self.app.pargs.with_tags:
                    out += ' [{tag}]'
                if out != '{key}':
                    # read the full content from key
                    value, expire_time, tag = self.app.cache._cache.get(key, default=None, expire_time=True, tag=True, retry=False)
                    if value is None:
                        value = ''
                    if expire_time is None:
                        expire_time = ''
                    if tag is None:
                        tag = ''
                    # check if need to compare with tag filter from command line
                    if self.app.pargs.tag is not None:
                        if self.app.pargs.tag == '':
                            _show = tag == ''
                        else:
                            _show = tag == self.app.pargs.tag
                else:
                    # just empty the values
                    value, expire_time, tag = (None, None, None)

            # show the line
            if _show:
                print(out.format(key=key, value=value, expire_time=expire_time, tag=tag))

    @ex(
        help='purge the cache',
        description='Purge current cache content from diskcache.',
        epilog=f'Use "{basename(sys.argv[0])} cache purge" with options like "--tag" to delete only partial content from cache.',
        arguments=[
            (
                ['--tag'],
                dict(
                    action='store',
                    help='show keys for tag only',
                ),
            ),
        ],
    )
    def purge(self):
        self.app.cache.set('hallo','Huhu')
        # check if parameter for tag
        if self.app.pargs.tag is not None:
            if self.app.pargs.tag == '':
                print('Can not purge from cache with empty tag. Need to be purged completely.')
                return
            else:
                num = self.app.cache.evict(self.app.pargs.tag, retry=True)
        else:
          num = self.app.cache.purge(retry=True)

        print(f'Purged {num} items from cache.')

    @ex(
        help='delete keys from cache content',
        description='Delete keys from the current cache content from diskcache.',
        epilog=f'Use "{basename(sys.argv[0])} cache delete" with options like "regex.*" to delete all matching keys from cache.',
        arguments=[
            (
                ['keys'],
                dict(
                    action='store',
                    nargs='+',
                    help='delete keys like regular expressions',
                ),
            ),
        ],
    )
    def delete(self):
        num = 0
        # iter over all keys stored in cache
        for key in self.app.cache._cache.iterkeys():
            _delete = False
            for s_key in self.app.pargs.keys:
                if re.search(s_key, key):
                    _delete = True
                    break

            if _delete:
                if self.app.cache.delete(key, retry=True):
                    num += 1
                    print(f'Deleted: {key}')
                else:
                    print(f'Error: {key}')

        print(f'In total {num} keys deleted.')

    @ex(
        help='set key with value',
        description='Set key with on current cache with optional tag.',
        epilog=f'Use "{basename(sys.argv[0])} cache set key value" with options like "--tag" to save value with tags on cache.',
        arguments=[
            (
                ['key'],
                dict(
                    action='store',
                    nargs=1,
                    help='use key',
                ),
            ),
            (
                ['--value'],
                dict(
                    action='store',
                    required=True,
                    help='set value',
                ),
            ),
            (
                ['--tag'],
                dict(
                    action='store',
                    help='use tag for key',
                    default=None,
                ),
            ),
        ],
    )
    def set(self):
        key = self.app.pargs.key[0]
        value = self.app.pargs.value
        tag = self.app.pargs.tag
        if self.app.cache.set(key, value, tag=tag, retry=True):
            print(f'Set: {key}')
        else:
            print(f'Error: {key}')

    @ex(
        help='get value by key',
        description='Get value by key from current cache.',
        epilog=f'Use "{basename(sys.argv[0])} cache get key" to get the value identified by key.',
        arguments=[
            (
                ['key'],
                dict(
                    action='store',
                    nargs=1,
                    help='read from key',
                ),
            ),
            (
                ['--default'],
                dict(
                    action='store',
                    help='define a default value when not available',
                    default=None,
                ),
            ),
        ],
    )
    def get(self):
        key = self.app.pargs.key[0]
        default = self.app.pargs.default
        value = self.app.cache.get(key, default=default, retry=True)
        print(f'{key} = {value}')


def load(app):
    app._meta.cache_handler = TokeoDiskCacheCacheHandler.Meta.label
    app.handler.register(TokeoDiskCacheCacheHandler)
    app.handler.register(TokeoDiskCacheController)


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
