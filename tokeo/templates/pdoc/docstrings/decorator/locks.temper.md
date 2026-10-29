The *.locks.temper decorator is a Resource-limiting decorator to
control concurrent access to a function.

This decorator implements a semaphore-like mechanism that limits the
number of concurrent calls to a function across processes or threads.
Unlike throttle which focuses on rate over time, temper focuses on
limiting concurrent usage. It uses DiskCache to maintain state, making
it suitable for distributed applications.

When the concurrent limit is reached, the decorator will either:

1. Block and sleep until a slot becomes available
1. Call an alternative callback function if cb_on_locked is provided

The decorator keeps the deadlines of the holders. Each call adds its own
deadline, now plus expire, and removes it when the function completes.

### Args

- **count** (int): Maximum number of concurrent calls allowed, at least 1
- **name** (str, optional): Custom cache key name for this temper instance
- **name_f** (str, optional): Format string to generate key name based on function arguments
- **expire** (float, optional): Seconds a call holds its slot at most, longer than the longest call. Defaults to 3600.
- **sleep_func** (callable, optional): Sleep function on resource limit. Defaults to time.sleep.
- **cb_on_locked** (callable, optional): Function to call on limit instead of waiting
- **verbose** (bool, optional): Whether to log tempering information. Defaults to True.

### Returns

- **callable**: The decorated function with concurrency control applied

### Example

```python
@app.cache.locks.temper(count=3)
def resource_intensive_operation(data):
    # At most 3 instances of this function can run concurrently
    # across all processes/threads that share the same cache
    return process_large_dataset(data)
```

### Notes

- The temper state persists in the cache based on the function
    name or custom name/name_f parameter
- If a process crashes while holding a slot, the slot is released at
    its own deadline, preventing permanent deadlock
- ```temper``` never stops a running call, also not after its expire; a call
    that must end in time needs its own limit, e.g. the time_limit of a
    dramatiq actor below expire
- Unlike throttle, temper has a fixed delay when blocked
    (0.05 seconds by default)
- The function will automatically restore slots when complete, ensuring
    resources become available again
