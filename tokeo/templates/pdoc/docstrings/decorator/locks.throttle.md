The *.locks.throttle decorator is a Resource-limiting decorator to
control concurrent access to a function.

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
