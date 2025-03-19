"""
Disk-based caching system with advanced locking, throttling, and rate limiting.

This module extends the standard diskcache library to provide a robust caching
system with additional features including:

1. Distributed locks for synchronization across processes
2. Rate limiting decorators for API calls and resource management
3. Concurrency control for resource-intensive operations
4. Command-line interface for cache inspection and management

The module implements two main rate-control mechanisms:
- throttle: Limits the rate of function calls over time (token bucket algorithm)
- temper: Limits the number of concurrent function calls (semaphore-like)

Both mechanisms are distributed and work across multiple processes, making them
suitable for web applications, microservices, and other distributed systems.

Example:
    ```python
    # Configure and use the cache
    from tokeo.ext import diskcache

    # Access cache through Cement app
    app.cache.set('key', 'value', expire=3600)
    value = app.cache.get('key')

    # Use rate limiting decorators
    @app.cache.locks.throttle(count=5, per_seconds=60)
    def api_call(resource_id):
        # Limited to 5 calls per minute
        return make_external_api_request(resource_id)

    @app.cache.locks.temper(count=3)
    def heavy_operation(data):
        # Limited to 3 concurrent executions
        return process_large_dataset(data)
    ```
"""

import sys
from os.path import basename
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
    """
    Exception raised for errors during lock operations.

    This exception is raised when lock acquisition, release, or other
    lock-related operations fail in the distributed locking system.

    """

    pass


class TokeoDiskCacheLocksHandler:
    """
    Handles distributed locks and rate limiting using disk cache.

    This class provides a distributed locking system and rate limiting
    mechanisms that work across multiple processes and threads by using
    disk-based cache for synchronization. It includes methods for:

    - Acquiring and releasing locks
    - Lock context management
    - Rate limiting (throttle decorator)
    - Concurrency control (temper decorator)

    All operations are distributed and work across multiple processes,
    making them suitable for web applications and microservices.

    ### Attributes:

    - **app** (Application): The Cement application instance
    - **_cache** (diskcache.Cache): The diskcache.Cache instance used for storage
    - **_tag** (str): Tag used to identify locks in the cache
    - **_key_prefix** (str): Prefix added to all lock keys for namespace isolation

    """

    def __init__(self, app, cache, tag, key_prefix):
        """
        Initialize the locks handler.

        ### Args:

        - **app** (Application): The Cement application instance
        - **cache** (diskcache.Cache): The diskcache.Cache instance to use
        - **tag** (str): Tag to identify locks in the cache
        - **key_prefix** (str): Prefix for lock keys

        """
        self.app = app
        self._cache = cache
        self._tag = tag
        self._key_prefix = key_prefix

    # --------------------------------------------------------------------------------------

    def purge(self):
        """
        Purge all locks managed by this handler.

        Removes all cache entries tagged with this handler's tag.

        ### Returns:

        - **int**: Number of locks purged from the cache

        """
        total = 0
        while True:
            num = self._cache.evict(self._tag, retry=True)
            if num > 0:
                total += num
            else:
                break

        return total

    def delete(self, key, **kw):
        """
        Delete a specific lock by key.

        ### Args:

        - **key** (str): The lock key to delete
        - ****kw**: Additional keyword arguments passed to cache.delete

        ### Returns:

        - **bool**: True if the lock was deleted, False otherwise

        """
        return self._cache.delete(self._key_prefix + key, retry=True)

    def acquire(self, key, expire=None):
        """
        Acquire a lock with the given key.

        ### Args:

        - **key** (str): The lock key to acquire
        - **expire** (float, optional): Time in seconds after which the lock
          automatically expires. Defaults to None.

        ### Returns:

        - **bool**: True if the lock was acquired, False otherwise

        """
        lock = diskcache.Lock(self._cache, self._key_prefix + key, expire=expire, tag=self._tag)
        return lock.acquire()

    def release(self, key):
        """
        Release a previously acquired lock.

        ### Args:

        - **key** (str): The lock key to release

        ### Raises:

        - **LockError**: If the lock cannot be released

        """
        lock = diskcache.Lock(self._cache, self._key_prefix + key, tag=self._tag)
        lock.release()

    def locked(self, key):
        """
        Check if a lock is currently held.

        ### Args:

        - **key** (str): The lock key to check

        ### Returns:

        - **bool**: True if the lock is held, False otherwise

        """
        lock = diskcache.Lock(self._cache, self._key_prefix + key, tag=self._tag)
        return lock.locked()

    @contextmanager
    def lock(self, key, expire=None):
        """
        Context manager for acquiring and releasing a lock.

        Provides a convenient way to use locks in a with statement.
        The lock is automatically released when exiting the context.

        ### Args:

        - **key** (str): The lock key to acquire
        - **expire** (float, optional): Time in seconds after which the lock
          automatically expires. Defaults to None.

        ### Yields:

        - **None**: Control is yielded to the block inside the with statement

        ### Raises:

        - **LockError**: If the lock cannot be acquired or released

        ### Example:

        ```python
        with app.cache.locks.lock('critical-section'):
            # This code runs with exclusive access
            perform_critical_operation()
        # Lock is automatically released here
        ```
        """
        try:
            lock = diskcache.Lock(self._cache, self._key_prefix + key, expire=expire, tag=self._tag)
            lock.acquire()
            yield
        finally:
            lock.release()

    # --------------------------------------------------------------------------------------

    def throttle(
        self,
        count=1,
        per_seconds=1,
        name=None,
        name_f=None,
        expire=None,
        time_func=time.time,
        sleep_func=time.sleep,
        cb_on_locked=None,
        verbose=True,
    ):
        """
        Rate-limiting decorator to throttle function calls to a specified frequency.

        This decorator implements a token bucket algorithm to limit how often
        a function can be called, distributing calls evenly over time. It uses
        DiskCache to maintain state across multiple processes and threads, making
        it suitable for distributed applications.

        When the rate limit is exceeded, the decorator will either:
        1. Block and sleep until enough tokens are available to call the function
        2. Call an alternative callback function if cb_on_locked is provided

        ### Args:

        - **count** (int): Number of calls allowed in the specified time period
        - **per_seconds** (float): Time period in seconds over which to limit calls
        - **name** (str, optional): Custom cache key name for this throttle
        - **name_f** (str, optional): Format string to generate key name based on function arguments
        - **expire** (float, optional): Expiration time for the throttle key in seconds
        - **time_func** (callable, optional): Function to get current time. Defaults to time.time.
        - **sleep_func** (callable, optional): Function to sleep when rate limited. Defaults to time.sleep.
        - **cb_on_locked** (callable, optional): Function to call when rate limited instead of waiting
        - **verbose** (bool, optional): Whether to log throttling information. Defaults to True.

        ### Returns:

        - **callable**: The decorated function with rate limiting applied

        ### Example:

        ```python
        @app.cache.locks.throttle(count=5, per_seconds=60)
        def limited_api_call(resource_id):
            # This function can be called at most 5 times per minute
            return make_external_api_request(resource_id)
        ```

        ### Notes:

        - The throttle state persists in the cache based on the function name
          or custom name/name_f parameter
        - When rate-limited, execution will be delayed unless cb_on_locked
          is provided
        - Token bucket algorithm allows for bursts up to 'count' calls at once

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
                    func_name=func.__name__,
                    func_full_name=func_full_name,
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
                if verbose:
                    self.app.log.info(f'@throttle {func.__name__} using key {key}')

                # loop
                while True:
                    # run in a transaction<
                    with self._cache.transact(retry=True):
                        # get values from cache
                        values = self._cache.get(key)
                        # check if already a valid initialized tuple(int, int)
                        # exist where ints must have values > 0
                        if type(values) is tuple and len(values) == 2 and type(values[0]) is int and type(values[1]) is int:
                            # expand the cached tuple values
                            last, tally = values
                            # validate the values
                            if last < 1 or tally < 1:
                                # invalidate the tuple
                                values = None
                        else:
                            # invalidate any other values read or not from cache
                            values = None
                        # on read failure or values failure, reset the values
                        if values is None:
                            # initialize the variables
                            last = time_func()
                            tally = count
                            # initialize the cache immediately
                            self._cache.set(key, (last, tally), expire=expire, tag=self._tag)

                        # calc the next values
                        now = time_func()
                        tally += (now - last) * rate
                        delay = 0

                        if tally > count:
                            self._cache.set(key, (now, count - 1), expire=expire, tag=self._tag)
                        elif tally >= 1:
                            self._cache.set(key, (now, tally - 1), expire=expire, tag=self._tag)
                        else:
                            delay = (1 - tally) / rate

                    if delay:
                        # break and call callback outside the transaction
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

    # --------------------------------------------------------------------------------------

    def temper(
        self,
        count=1,
        name=None,
        name_f=None,
        expire=None,
        sleep_func=time.sleep,
        cb_on_locked=None,
        verbose=True,
    ):
        """
        Resource-limiting decorator to control concurrent access to a function.

        This decorator implements a semaphore-like mechanism that limits the
        number of concurrent calls to a function across processes or threads.
        Unlike throttle which focuses on rate over time, temper focuses on
        limiting concurrent usage. It uses DiskCache to maintain state, making
        it suitable for distributed applications.

        When the concurrent limit is reached, the decorator will either:
        1. Block and sleep until a slot becomes available
        2. Call an alternative callback function if cb_on_locked is provided

        The decorator maintains a counter of available slots. Each call decrements
        the counter. When the function completes, the counter is incremented again.

        ### Args:

        - **count** (int): Maximum number of concurrent calls allowed
        - **name** (str, optional): Custom cache key name for this temper instance
        - **name_f** (str, optional): Format string to generate key name based on function arguments
        - **expire** (float, optional): Expiration time for the temper key in seconds
        - **sleep_func** (callable, optional): Sleep function on resource limit. Defaults to time.sleep.
        - **cb_on_locked** (callable, optional): Function to call on limit instead of waiting
        - **verbose** (bool, optional): Whether to log tempering information. Defaults to True.

        ### Returns:

        - **callable**: The decorated function with concurrency control applied

        ### Example:

        ```python
        @app.cache.locks.temper(count=3)
        def resource_intensive_operation(data):
            # At most 3 instances of this function can run concurrently
            # across all processes/threads that share the same cache
            return process_large_dataset(data)
        ```

        ### Notes:

        - The temper state persists in the cache based on the function
          name or custom name/name_f parameter
        - If a process crashes while holding a slot, the slot will be released
          when the cache key expires, preventing permanent deadlock
        - Unlike throttle, temper has a fixed delay when blocked
          (0.05 seconds by default)
        - The function will automatically restore slots when complete, ensuring
          resources become available again

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
                    func_name=func.__name__,
                    func_full_name=func_full_name,
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
                if verbose:
                    self.app.log.info(f'@temper {func.__name__} using key {key}')

                # loop
                while True:
                    # run in a transaction
                    with self._cache.transact(retry=True):
                        # get value from cache
                        value = self._cache.get(key)
                        # check if already correctly initialized
                        if type(value) is int and value > 0:
                            # expand the cached value
                            available = value
                        else:
                            # re-initialize the variables
                            available = count
                            # initialize the cache immediately
                            self._cache.set(key, available, expire=expire, tag=self._tag)

                        # calc the next values
                        delay = 0

                        if available >= 1:
                            self._cache.set(key, available - 1, expire=expire, tag=self._tag)
                        else:
                            delay = 0.05

                    if delay:
                        # break and call callback outside the transaction
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
                    self._cache.set(key, self._cache.get(key, default=count) + 1, expire=expire, tag=self._tag)

                # return the @decorated result
                return result

            return wrapper

        return decorator

    # --------------------------------------------------------------------------------------


class TokeoDiskCacheCacheHandler(cache.CacheHandler):
    """
    Tokeo DiskCache handler providing persistent caching and locking.

    This class implements the Cement Cache Handler interface using the diskcache
    library. It extends the basic caching functionality with:

    - Distributed locking mechanisms
    - Rate limiting capabilities
    - Advanced cache management operations
    - Enhanced control over cache expiration and tagging

    The handler provides both standard cache operations (get, set, delete, purge)
    and advanced features through the locks attribute, which gives access to
    throttling and tempering decorators.

    ### Attributes:

    - **locks** (TokeoDiskCacheLocksHandler): Handler for locks and rate limiting
    - **_cache** (diskcache.Cache): Underlying diskcache instance

    """

    class Meta:
        """Handler meta-data and configuration defaults."""

        #: Unique identifier for this handler
        label = 'tokeo.diskcache'

        #: Id for config
        config_section = 'diskcache'

        #: Dict with initial settings
        config_defaults = dict(
            # Directory where cache files are stored (None = auto-determine)
            directory=None,
            # Default timeout for cache operations in seconds
            timeout=60,
            # Tag used to identify lock entries in the cache
            locks_tag='diskcache_locks',
            # Prefix added to all lock keys for namespace isolation
            locks_key_prefix='dc_',
        )

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._cache = None

    def _setup(self, *args, **kw):
        """
        Initialize the cache and locks handler.

        Called by Cement during handler initialization. Sets up the disk cache
        and the locks handler based on configuration.

        ### Args:

        - ***args**: Positional arguments passed to the parent class
        - ****kw**: Keyword arguments passed to the parent class

        """
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

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a simple wrapper around the application's config.get method.

        ### Args:

        - **key** (str): Configuration key to retrieve
        - ****kwargs**: Additional arguments passed to config.get()

        ### Returns:

        - **any**: The configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def locks_handler(self, tag, locks_key_prefix):
        """
        Create a new locks handler instance.

        Creates a TokeoDiskCacheLocksHandler that provides distributed locking
        and rate limiting functionality.

        ### Args:

        - **tag** (str): Tag to identify locks in the cache
        - **locks_key_prefix** (str): Prefix for lock keys

        ### Returns:

        - **TokeoDiskCacheLocksHandler**: The locks handler instance

        """
        return TokeoDiskCacheLocksHandler(self.app, self._cache, tag, locks_key_prefix)

    def get(self, key, default=None, **kw):
        """
        Get a value from the cache.

        ### Args:

        - **key** (str): The key of the item in the cache to get
        - **default** (any, optional): The value to return if the item is not found.
          Defaults to None.
        - ****kw**: Additional keyword arguments
          - **read** (bool): Whether to read without updating cache statistics
          - **retry** (bool): Whether to retry if the database is locked

        ### Returns:

        - **any**: The value of the item in the cache, or the default value

        """
        read = kw.get('read', False)
        retry = kw.get('retry', False)
        return self._cache.get(key, default, read=read, retry=retry)

    def set(self, key, value, **kw):
        """
        Set a value in the cache for the given key.

        ### Args:

        - **key** (str): The key of the item in the cache to set
        - **value** (any): The value of the item to set
        - ****kw**: Additional keyword arguments
            - **expire** (float): Expiration time in seconds for the cache item
            - **tag** (str): Tag to categorize this cache item
            - **read** (bool): Whether to read without updating cache statistics
            - **retry** (bool): Whether to retry if the database is locked

        ### Returns:

        - **bool**: True if the item was set successfully

        """

        expire = kw.get('expire', None)
        tag = kw.get('tag', None)
        read = kw.get('read', False)
        retry = kw.get('retry', False)
        return self._cache.set(key, value, expire=expire, tag=tag, read=read, retry=retry)

    def delete(self, key, **kw):
        """
        Delete an item from the cache for the given key.

        ### Args:

        - **key** (str): The key to delete from the cache
        - ****kw**: Additional keyword arguments
            - **retry** (bool): Whether to retry if the database is locked

        ### Returns:

        - **bool**: True if the item was deleted successfully

        """
        retry = kw.get('retry', False)
        return self._cache.delete(key, retry=retry)

    def purge(self, **kw):
        """
        Purge the entire cache, removing all keys and values.

        ### Args:

        - ****kw**: Additional keyword arguments
            - **retry** (bool): Whether to retry if the database is locked

        ### Returns:

        - **bool**: True if the cache was successfully purged

        """
        retry = kw.get('retry', False)
        return self._cache.clear(retry=retry)

    # --------------------------------------------------------------------------------------

    def check(self, fix=False, retry=False):
        """
        Check the cache's database integrity.

        Verifies integrity and optionally fixes any corruption in the database.

        ### Args:

        - **fix** (bool, optional): Whether to fix any corruption found. Defaults to False.
        - **retry** (bool, optional): Whether to retry if the database is locked. Defaults to False.

        ### Returns:

        - **dict**: Information about database integrity status

        """
        return self._cache.check(fix=fix, retry=retry)

    # create an alias for purge to original
    clear = purge

    def evict(self, tag, retry=False):
        """
        Remove all items with the specified tag.

        ### Args:

        - **tag** (str): Tag that identifies items to evict
        - **retry** (bool, optional): Whether to retry if the database is locked. Defaults to False.

        ### Returns:

        - **int**: Number of items evicted from the cache

        """
        total = 0
        while True:
            num = self._cache.evict(tag, retry=True)
            if num > 0:
                total += num
            else:
                break

        return total

    def expire(self, now=None, retry=False):
        """
        Remove all expired items from the cache.

        ### Args:

        - **now** (float, optional): Current time to use for expiration check. Defaults to None (uses current time).
        - **retry** (bool, optional): Whether to retry if the database is locked. Defaults to False.

        ### Returns:

        - **int**: Number of expired items removed from the cache

        """
        total = 0
        while True:
            num = self._cache.expire(now=now, retry=retry)
            if num > 0:
                total += num
            else:
                break

        return total

    def add(self, key, value, expire=None, tag=None, read=False, retry=False):
        """
        Add a key-value item to the cache only if the key doesn't exist.

        ### Args:

        - **key** (str): The key to add to the cache
        - **value** (any): The value to add to the cache
        - **expire** (float, optional): Expiration time in seconds. Defaults to None.
        - **tag** (str, optional): Tag to categorize this cache item. Defaults to None.
        - **read** (bool, optional): Whether to read without updating cache statistics. Defaults to False.
        - **retry** (bool, optional): Whether to retry if the database is locked. Defaults to False.

        ### Returns:

        - **bool**: True if the item was added successfully, False if the key already exists

        """
        return self._cache.add(key, value, expire=expire, tag=tag, read=read, retry=retry)

    def touch(self, key, expire=None, retry=False):
        """
        Update the expiration time on an existing key.

        ### Args:

        - **key** (str): The key to update
        - **expire** (float, optional): New expiration time in seconds. Defaults to None.
        - **retry** (bool, optional): Whether to retry if the database is locked. Defaults to False.

        ### Returns:

        - **bool**: True if the expiration time was updated

        """
        return self._cache.touch(key, expire=expire, retry=retry)

    def transact(self, retry=False):
        """
        Create a transaction context manager for atomic operations.

        ### Args:

        - **retry** (bool, optional): Whether to retry if the database is locked. Defaults to False.

        ### Returns:

        - **context manager**: Transaction context for atomic cache operations

        """
        return self._cache.transact(retry=retry)

    def stats(self, reset=False):
        """
        Get cache statistics.

        ### Args:

        - **reset** (bool, optional): Whether to reset statistics after reading. Defaults to False.

        ### Returns:

        - **dict**: Dictionary with cache statistics

        """
        return self._cache.stats(reset=reset)

    def volume(self):
        """
        Get total size of cache on disk.

        ### Returns:

        - **int**: Size of the cache in bytes

        """
        return self._cache.volume()


class TokeoDiskCacheController(Controller):
    """
    Command-line controller for managing the disk cache.

    Provides a CLI interface for inspecting and manipulating the disk cache
    contents. This controller includes commands for:

    - Checking and maintaining the cache
    - Listing cache contents with various filters
    - Managing locks in the cache
    - Purging or deleting specific cache entries
    - Setting and retrieving cache values

    These commands allow administrators to debug and manage the cache state
    directly from the command line, which is especially useful in production
    environments when troubleshooting rate limiting or cache issues.
    """

    class Meta:
        """Meta configuration for the DiskCache controller."""

        label = 'cache'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'manage the cache content'
        description = 'Provides command-line interfaces to manage diskcache content.'
        epilog = f'Example: {basename(sys.argv[0])} cache command --options'

    # --------------------------------------------------------------------------------------

    @ex(
        help='verify the cache',
        description='Maintain and verify the diskcache cache.',
        epilog=f'Use "{basename(sys.argv[0])} cache check" to drop expired items and check the cache.',
    )
    def check(self):
        """
        Verify and maintain the cache database.

        Performs a series of maintenance operations:
        1. Checks and fixes database integrity
        2. Reports cache usage statistics
        3. Removes expired items
        4. Counts current cache entries

        This command is useful for regular maintenance and troubleshooting.

        ### Notes:

        : All operations are performed with database locking retry enabled
          to ensure reliability even when the cache is under heavy use.

        """
        directory = self.app.cache._cache.directory
        self.app.print(f'Check cache at "{directory}".')
        vol = self.app.cache.volume()
        self.app.print(f'Used {vol} bytes on volume.')
        result = self.app.cache.check(fix=True, retry=True)
        self.app.print(f'Results from cache fix:\n    {result}')
        num = self.app.cache.expire(retry=True)
        self.app.print(f'Removed {num} expired items from cache.')
        cnt = self.app.cache._cache.__len__()
        self.app.print(f'Currently {cnt} keys in cache.')

    # --------------------------------------------------------------------------------------

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
                ['--with-types'],
                dict(
                    action='store_true',
                    help='show types for key values',
                ),
            ),
            (
                ['--with-expires'],
                dict(
                    action='store_true',
                    help='show expires time for keys',
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
        """
        List and filter cache contents.

        Displays cache entries with various optional information:
        - Key values (with --with-values)
        - Value types (with --with-types)
        - Expiration times (with --with-expires)
        - Tags (with --with-tags)

        Entries can be filtered by regular expression patterns against keys
        or by tag.

        ### Notes:

        - Expired entries are automatically removed before listing
        - When no filters are provided, all cache entries are shown
        - Use multiple filter options together for more precise results

        """
        # refresh the cache and drop all expired keys
        _ = self.app.cache.expire(retry=True)
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
                if self.app.pargs.with_types:
                    out += ' |:{value_type}|'
                if self.app.pargs.with_expires:
                    out += ' ({expire_time})s'
                if self.app.pargs.with_tags:
                    out += ' [{tag}]'
                if out != '{key}' or self.app.pargs.tag is not None:
                    # read the full content from key
                    value, expire_time, tag = self.app.cache._cache.get(key, default=None, expire_time=True, tag=True, retry=False)
                    if value is None:
                        value = ''
                    if expire_time is None:
                        expire_time = 'no expire'
                    else:
                        expire_time = expire_time - time.time()
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
                self.app.print(out.format(key=key, value=value, value_type=type(value).__name__, expire_time=expire_time, tag=tag))

    # --------------------------------------------------------------------------------------

    @ex(
        help='access the locks from cache',
        description='Handle locks stored in diskcache.',
        epilog=f'Use "{basename(sys.argv[0])} cache locks" to handle the locks in diskcache.',
        arguments=[
            (
                ['--purge'],
                dict(
                    action='store_true',
                    help='purge the locks from cache',
                ),
            ),
        ],
    )
    def locks(self):
        """
        Manage and view distributed locks in the cache.

        With no options, displays all active locks with their values, types,
        expiration times, and tags. When used with --purge, removes all locks
        from the cache.

        This command is particularly useful for troubleshooting stuck locks
        or deadlocks in the rate limiting and concurrency control systems.

        ### Notes:

        - Lock entries are identified by the lock tag configured in the application
        - Purging locks can resolve deadlocks but may disrupt running operations

        """
        # check if parameter for locks cache
        if self.app.pargs.purge:
            num = self.app.cache.locks.purge()
            self.app.print(f'Purged {num} locks from cache.')
        else:
            # use the list command to show the locks
            self.app.print(f'Locks using the tag: {self.app.cache.locks._tag}')
            self.app.pargs.keys = ''
            self.app.pargs.with_values = True
            self.app.pargs.with_tags = True
            self.app.pargs.with_types = True
            self.app.pargs.with_expires = True
            self.app.pargs.tag = self.app.cache.locks._tag
            self.list()

    # --------------------------------------------------------------------------------------

    @ex(
        help='purge the cache',
        description='Purge current cache content from diskcache.',
        epilog=f'Use "{basename(sys.argv[0])} cache purge" with options like "--tag" to delete only partial content from cache.',
        arguments=[
            (
                ['--tag'],
                dict(
                    action='store',
                    help='purge keys for tag only',
                ),
            ),
            (
                ['--all'],
                dict(
                    action='store_true',
                    help='purge all values',
                ),
            ),
        ],
    )
    def purge(self):
        """
        Remove items from the cache.

        Provides two modes of operation:
        1. Tag-based purging: Removes all items with a specific tag
        2. Complete purging: Removes all items from the cache

        This command requires either --tag or --all options to prevent
        accidental deletion of cache items.

        ### Notes:

        - The --tag and --all options are mutually exclusive
        - Tag-based purging is safer as it only removes a subset of items
        - Complete purging with --all removes everything, including locks

        """
        # check if valid parameters
        if self.app.pargs.tag is not None and self.app.pargs.all:
            self.app.log.error('Can not use both options --tag and --all together')
            self.app.exit_code = 1
            return

        # check if valid parameters
        if self.app.pargs.tag is None and not self.app.pargs.all:
            self.app.log.error('Missing mandatory option --tag or --all')
            self.app.exit_code = 1
            return

        # check for valid tag value
        if self.app.pargs.tag is not None:
            if self.app.pargs.tag == '':
                self.app.log.error('Can not purge from cache with empty tag. Use delete or purge completely in that case.')
                self.app.exit_code = 1
                return
            else:
                num = self.app.cache.evict(self.app.pargs.tag, retry=True)

        # check for all flag
        elif self.app.pargs.all:
            num = self.app.cache.purge(retry=True)

        # identify for feedback
        else:
            num = 0

        self.app.print(f'Purged {num} items from cache.')

    # --------------------------------------------------------------------------------------

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
        """
        Delete specific cache entries by key pattern.

        Removes cache entries whose keys match one or more regular expressions.
        This allows for selective deletion of related cache items without
        removing everything.

        Each matching key is displayed as it's deleted, with a total count
        of deleted items shown at the end.

        ### Notes:

        - Regular expressions are applied against the full key name
        - Multiple patterns can be provided to match different key formats
        - Error messages are displayed for keys that couldn't be deleted

        """
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
                    self.app.print(f'Deleted: {key}')
                else:
                    self.app.log.error(f'Error: {key}')

        self.app.print(f'In total {num} keys deleted.')

    # --------------------------------------------------------------------------------------

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
                ['--value-type'],
                dict(
                    action='store',
                    help='define type for value',
                    default='str',
                    required=False,
                    choices=['str', 'int', 'float', 'bool', 'eval'],
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
            (
                ['--expire'],
                dict(
                    action='store',
                    help='use expire time (float) for key',
                    default=None,
                    type=float,
                ),
            ),
        ],
    )
    def set(self):
        """
        Set a value in the cache.

        Creates or updates a cache entry with the specified key, value, and
        optional parameters like tag and expiration time.

        Supports different value types through the --value-type option:
        - str: String value (default)
        - int: Integer value
        - float: Floating-point value
        - bool: Boolean value
        - eval: Python expression to evaluate (use with caution)

        ### Notes:

        - The --value parameter is required
        - Type conversion errors are reported with details
        - The eval type should be used carefully as it executes Python code

        """
        key = self.app.pargs.key[0]
        value = self.app.pargs.value
        value_type = self.app.pargs.value_type
        tag = self.app.pargs.tag
        expire = self.app.pargs.expire
        # check the condition for value type
        typed_value = None
        try:
            if value_type == 'eval':
                typed_value = eval(value)
            elif value_type == 'int':
                typed_value = int(value)
            elif value_type == 'float':
                typed_value = float(value)
            elif value_type == 'bool':
                typed_value = bool(value)
            else:
                typed_value = value
        except Exception as err:
            self.app.log.error(f'Value could not be set as type "{value_type}"! ({err})')
            self.app.exit_code = 1
            return

        # set value in cache
        if self.app.cache.set(key, typed_value, expire=expire, tag=tag, retry=True):
            self.app.print(f'Set: {key}')
        else:
            self.app.log.error(f'Error: {key}')

    # --------------------------------------------------------------------------------------

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
        """
        Retrieve a value from the cache.

        Fetches and displays a cache entry by its key. If the key doesn't exist,
        the command can optionally return a default value.

        The value is displayed in the format "key = value" if it exists or
        if a default value was provided.

        ### Notes:

        - No output is produced if the key doesn't exist and no default is specified
        - Database locking retry is enabled for reliable reads
        - The command returns the raw Python value, not a string representation

        """
        key = self.app.pargs.key[0]
        default = self.app.pargs.default
        value = self.app.cache.get(key, default=default, retry=True)
        if value is not None:
            self.app.print(f'{key} = {value}')


def tokeo_diskcache_pdoc_render_decorator(app, func, decorator, args, kwargs):
    """
    Handle docstrings for dramatiq decorators in pdoc
    """
    if re.match(r'@app\.[^.]+\.locks\.throttle', decorator):
        params = None
        if kwargs is not None:
            try:
                if 'name' in kwargs:
                    params = f'name="{kwargs['name'].value}"'
                elif 'name_f' in kwargs:
                    params = f'name_f="{kwargs['name_f'].value}"'
                else:
                    params = f'name="{func.name}"'

                if 'count' in kwargs:
                    params += f', count={kwargs["count"].value}'
                if 'per_second' in kwargs:
                    params += f', per_second={kwargs["per_second"].value}'

            except Exception:
                pass

        return dict(
            decorator=decorator,
            params=params,
            docstring=app.pdoc.docstrings('decorator', 'locks.throttle'),
        )
    elif re.match(r'@app\.[^.]+\.locks\.temper', decorator):
        params = None
        if kwargs is not None:
            try:
                if 'name' in kwargs:
                    params = f'name="{kwargs['name'].value}"'
                elif 'name_f' in kwargs:
                    params = f'name_f="{kwargs['name_f'].value}"'
                else:
                    params = f'name="{func.name}"'

                if 'count' in kwargs:
                    params += f', count={kwargs["count"].value}'

            except Exception:
                pass

        return dict(
            decorator=decorator,
            params=params,
            docstring=app.pdoc.docstrings('decorator', 'locks.temper'),
        )


def load(app):
    """
    Load the DiskCache extension into the application.

    This function is called by Cement when loading extensions. It:

    1. Sets the default cache handler to TokeoDiskCacheCacheHandler
    2. Registers the cache handler with the application
    3. Registers the command-line controller for cache management

    ### Args:

    - **app** (Application): The Cement application instance

    """
    app._meta.cache_handler = TokeoDiskCacheCacheHandler.Meta.label
    app.handler.register(TokeoDiskCacheCacheHandler)
    app.handler.register(TokeoDiskCacheController)
    app.hook.register('tokeo_pdoc_render_decorator', tokeo_diskcache_pdoc_render_decorator)


def unpack_func_args(func, *args):
    """
    Convert positional arguments to a dictionary using function signature.

    This helper function takes a function and its positional arguments and
    creates a dictionary mapping parameter names to their values. This is
    used internally by the throttle and temper decorators to process
    function arguments.

    ### Args:

    - **func** (callable): The function whose arguments are being unpacked
    - ***args**: The positional arguments passed to the function

    ### Returns:

    - **dict**: A dictionary mapping parameter names to their values

    ### Notes:

    - Only handles positional arguments, not keyword arguments
    - If a parameter has a default value and no argument is provided,
      it will not be included in the returned dictionary
    - Used primarily for generating cache keys based on function arguments

    """
    # create a dict for return
    d = dict()
    # init loop var
    n = 0
    # get parameters from inspect
    for p in inspect.signature(func).parameters:
        try:
            d[p] = args[n]
            n += 1
        except Exception:
            # if some of positional args given as kwargs then those
            # values will come in kwargs dict, so all positional args
            # processed by now
            break
    # return
    return d
