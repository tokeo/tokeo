Limits the run time of the decorated function.

A normal function gets ```TokeoTimelimitExceeded``` raised into its own
thread when it runs longer than its limit. A coroutine function is cancelled
at its next ```await``` and raises ```TokeoTimelimitExceeded``` instead.

### Args

- **seconds** (float): The longest run time of a call, a number above 0

### Returns

- **callable**: The decorated function with its time limit

### Raises

- **ValueError**: If seconds is not a number above 0
- **TokeoTimelimitExceeded**: In the call, when it runs longer

### Example

```python
from tokeo.core.utils.timelimit import timelimit

@timelimit(60)
def job():
    ...
```

### Notes

- A normal function gets the exception only between two Python steps; a
    call blocked in C code, e.g. in a socket, a driver or a regex, gets it
    afterwards or not at all
- A coroutine stops only at an ```await```; blocking code in it runs on and
    blocks the event loop
- Each call of a normal function runs its own timer thread while it runs; on
    a crowded system check the task limits (pid_max, ulimit -u, pids.max,
    TasksMax)
- TokeoTimelimitExceeded derives from TokeoBaseException and not from
    Exception, so an ```except Exception``` in the function does not swallow
    it; ```finally``` blocks run as usual
- A function that catches BaseException itself and goes on swallows the
    exception, its limit is lost
- For a generator function the limit covers only the creation of the
    generator, not its iteration; limit the function that consumes it
- Dramatiq actors have their own ```time_limit``` option that works the same
    way; use ```@timelimit``` for functions that also run outside dramatiq
