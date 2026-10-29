"""
Tokeo Time Limit Module.

Limits the run time of a function by the decorator ```@time_limit```.

A normal function gets ```TokeoTimeLimitExceeded``` raised into its own
thread when it runs longer than its limit. A coroutine function is cancelled
at its next ```await``` and raises ```TokeoTimeLimitExceeded``` instead.

### Example

```python
from tokeo.core.utils.timelimit import time_limit, TokeoTimeLimitExceeded

@time_limit(60)
def job():
    ...

@time_limit(10)
async def page():
    ...
```

### Notes

: A normal function gets the exception only between two Python steps; a
    call blocked in C code, e.g. in a socket, a driver or a regex, gets it
    afterwards or not at all

: A coroutine stops only at an ```await```; blocking code in it runs on and
    blocks the event loop

: Each call of a normal function runs its own timer thread while it runs; on
    a crowded system check the task limits (pid_max, ulimit -u, pids.max,
    TasksMax)

: TokeoTimeLimitExceeded derives from TokeoBaseException and not from
    Exception, so an ```except Exception``` in the function does not swallow
    it; ```finally``` blocks run as usual

: A function that catches BaseException itself and goes on swallows the
    exception, its limit is lost

: For a generator function the limit covers only the creation of the
    generator, not its iteration; limit the function that consumes it

: dramatiq actors have their own ```time_limit``` option that works the same
    way; use ```@time_limit``` for functions that also run outside dramatiq

"""

import ctypes
import asyncio
import inspect
import functools
import threading
from tokeo.core.exc import TokeoBaseException


class TokeoTimeLimitExceeded(TokeoBaseException):
    """
    Raised in a call that runs longer than its time limit.

    ### Notes

    : Not an Exception, so ```except Exception``` and ```except TokeoError```
        do not catch it; catch it by its name or by TokeoBaseException

    """

    pass


def time_limit(seconds):
    """
    Decorator to limit the run time of a function.

    ### Args

    - **seconds** (float): The longest run time of a call, a number above 0

    ### Returns

    - **callable**: The decorated function with its time limit

    ### Raises

    - **ValueError**: If seconds is not a number above 0
    - **TokeoTimeLimitExceeded**: In the call, when it runs longer

    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
        raise ValueError(f'seconds must be a number above 0, not {seconds!r}')

    def decorator(func):
        # the message tells which function ran too long
        message = f'{func.__qualname__} exceeded its time limit of {seconds} s'

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                # cancel the call at its next await after the limit
                timeout = asyncio.timeout(seconds)
                try:
                    async with timeout:
                        # call the wrapped function
                        return await func(*args, **kwargs)
                except TimeoutError:
                    # a TimeoutError of the function itself goes on
                    if not timeout.expired():
                        raise
                    raise TokeoTimeLimitExceeded(message) from None

            return wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # the thread of the call gets the exception
            thread_id = ctypes.c_ulong(threading.get_ident())
            raised = []

            def abort():
                raised.append(True)
                ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, ctypes.py_object(TokeoTimeLimitExceeded))

            # an own timer for each call
            timer = threading.Timer(seconds, abort)
            timer.daemon = True
            timer.start()
            try:
                # call the wrapped function
                return func(*args, **kwargs)
            except TokeoTimeLimitExceeded:
                # raised by the own limit, give it the message
                if raised:
                    raise TokeoTimeLimitExceeded(message) from None
                # raised by the limit of an outer call, let it go on
                raise
            finally:
                # stop the timer and wait until a running abort is done
                timer.cancel()
                timer.join()
                # the call ended before the exception arrived, drop it
                if raised:
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, None)

        return wrapper

    return decorator
