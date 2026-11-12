The *.locks.temper decorator is a resource-limiting decorator to
control concurrent access to a function.

This decorator implements a semaphore-like mechanism that limits the
number of concurrent calls to a function across processes or threads.
Unlike throttle which focuses on rate over time, temper focuses on
limiting concurrent usage. It uses DiskCache to maintain state, making
it suitable for distributed applications.

When the concurrent limit is reached, the decorator will either:

1. Block and sleep until a slot becomes available
1. Call an alternative callback function if cb_on_locked is provided

The decorator keeps a slot per holder: its expire time, its pid and the start
time of its process. Each call adds its own slot, now plus expire, and removes
it when the function completes.

### Args

- **max_calls** (int): Maximum number of concurrent calls allowed, at least 1
- **name** (str, optional): Custom cache key name for this temper instance
- **name_format** (str, optional): Format string to generate key name based on function
    arguments
- **expire** (float, optional): Seconds a call holds its slot at most, longer than
    the longest call. Defaults to 3600.
- **sleep_func** (callable, optional): Sleep function on resource limit. Defaults to
    time.sleep.
- **sleep_seconds** (float, optional): Seconds to wait between retries while
    blocked. Defaults to the config ```locks_sleep_seconds```, 0.1.
- **cb_on_locked** (callable, optional): Function to call on limit instead of
    waiting
- **verbose** (bool, optional): Whether to log tempering information. Defaults to
    False.

### Returns

- **callable**: The decorated function with concurrency control applied

### Example

```python
@app.cache.locks.temper(max_calls=3)
def resource_intensive_operation(data):
    # At most 3 instances of this function can run concurrently
    # across all processes/threads that share the same cache
    return process_large_dataset(data)
```

### Notes

: The temper state persists in the cache based on the function
    name or custom name/name_format parameter

: If a process crashes while holding a slot, the slot is freed as soon as
    a call finds no free slot, at the latest at its own expire time

: ```temper``` never stops a running call, also not after its expire; a call
    that must end in time needs its own limit below expire, e.g.
    ```@timelimit``` or the time_limit of a dramatiq actor

: Unlike throttle, temper has a fixed delay when blocked
    (sleep_seconds, 0.1 seconds by default)

: The function will automatically restore slots when complete, ensuring
    resources become available again
