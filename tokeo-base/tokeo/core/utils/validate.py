"""
Tokeo Validate Module.

The ```is_``` checks tell by True or False whether a value fits. They never
raise and never convert, so the caller raises its own error with its own
message. ```valid_or_default``` hands back a value that passes a check, else
a default. The ```as_``` functions convert a value into one form.

### Example

```python
from tokeo.core.utils.validate import is_number_gt, is_list, valid_or_default

if not is_number_gt(sleep_time, 0):
    raise ValueError(f'sleep_time must be a number above 0, not {sleep_time!r}')

names = valid_or_default(app.cache.get('names'), is_list, [])
```

### Notes

: bool is a subclass of int in Python, but ```True``` or ```False``` is never
    meant as number, so the int and number checks refuse it

: inf and nan are no numbers for the checks, a limit can never be met by
    them

: A string is never a number, also not ```'0.05'```; convert it first, e.g.
    by ```as_int_or_auto```

: ```None``` fails every check; where it is allowed, the caller tells it by
    ```value is None or is_int_gt(value, 0)```

"""

import math


def is_basetype(obj):
    """
    Tell whether the object is one of the Python base types.

    ### Args

    - **obj** (any): The object to check

    ### Returns

    - **bool**: True if the object is of a base type, False otherwise

    ### Notes

    - Base types: int, str, float, bool, bytes, list, dict, set, tuple
    - ```bool``` is listed explicitly for readability even though it is a
        subclass of int and would already match via int

    """
    basetypes = (int, str, float, bool, bytes, list, dict, set, tuple)
    return isinstance(obj, basetypes)


def is_bool(value):
    """
    Tell whether the value is True or False, not 1, 0 or a string.

    ### Args

    - **value** (any): The value to check

    ### Returns

    - **bool**: True for a bool

    """
    return isinstance(value, bool)


def is_int(value):
    """
    Tell whether the value is an int, not a bool.

    ### Args

    - **value** (any): The value to check

    ### Returns

    - **bool**: True for an int

    """
    return isinstance(value, int) and not isinstance(value, bool)


def is_int_gt(value, limit):
    """
    Tell whether the value is an int greater than limit.

    ### Args

    - **value** (any): The value to check
    - **limit** (int|float): The value must be greater

    ### Returns

    - **bool**: True for an int above limit

    """
    return is_int(value) and value > limit


def is_int_ge(value, limit):
    """
    Tell whether the value is an int greater than or equal to limit.

    ### Args

    - **value** (any): The value to check
    - **limit** (int|float): The lowest allowed value

    ### Returns

    - **bool**: True for an int of at least limit

    """
    return is_int(value) and value >= limit


def is_int_between(value, low, high):
    """
    Tell whether the value is an int from low to high, both included.

    ### Args

    - **value** (any): The value to check
    - **low** (int|float): The lowest allowed value
    - **high** (int|float): The highest allowed value

    ### Returns

    - **bool**: True for an int from low to high

    """
    return is_int(value) and low <= value <= high


def is_number(value):
    """
    Tell whether the value is an int or a finite float, not a bool.

    ### Args

    - **value** (any): The value to check

    ### Returns

    - **bool**: True for an int or a float that is neither inf nor nan

    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    # an int is always finite, math.isfinite would overflow on a huge one
    return isinstance(value, int) or math.isfinite(value)


def is_number_gt(value, limit):
    """
    Tell whether the value is a number greater than limit.

    ### Args

    - **value** (any): The value to check
    - **limit** (int|float): The value must be greater

    ### Returns

    - **bool**: True for an int or a float above limit

    """
    return is_number(value) and value > limit


def is_number_ge(value, limit):
    """
    Tell whether the value is a number greater than or equal to limit.

    ### Args

    - **value** (any): The value to check
    - **limit** (int|float): The lowest allowed value

    ### Returns

    - **bool**: True for an int or a float of at least limit

    """
    return is_number(value) and value >= limit


def is_number_between(value, low, high):
    """
    Tell whether the value is a number from low to high, both included.

    ### Args

    - **value** (any): The value to check
    - **low** (int|float): The lowest allowed value
    - **high** (int|float): The highest allowed value

    ### Returns

    - **bool**: True for an int or a float from low to high

    """
    return is_number(value) and low <= value <= high


def is_filled_str(value):
    """
    Tell whether the value is a string with more than whitespace.

    ### Args

    - **value** (any): The value to check

    ### Returns

    - **bool**: True for a string that is neither empty nor only whitespace

    """
    return isinstance(value, str) and value.strip() != ''


def is_list(value):
    """
    Tell whether the value is a list or a tuple.

    ### Args

    - **value** (any): The value to check

    ### Returns

    - **bool**: True for a list or a tuple, also an empty one

    """
    return isinstance(value, (list, tuple))


def is_list_of(value, check):
    """
    Tell whether the value is a list or a tuple whose entries all pass check.

    ### Args

    - **value** (any): The value to check
    - **check** (callable): Takes one entry and tells by True or False
        whether it fits, e.g. ```is_filled_str```

    ### Returns

    - **bool**: True for a list or a tuple of fitting entries, also an
        empty one

    ### Example

    ```python
    is_list_of(['a', 'b'], is_filled_str)
    is_list_of([1, 2], lambda entry: is_int_gt(entry, 0))
    ```

    """
    return is_list(value) and all(check(entry) for entry in value)


def valid_or_default(value, check, default=None):
    """
    Hand back the value when it passes check, else default.

    ### Args

    - **value** (any): The value to check
    - **check** (callable): Takes the value and tells by True or False
        whether it fits, e.g. ```is_list```
    - **default** (any, optional): Handed back when the value does not
        fit. Defaults to None

    ### Returns

    - **any**: The value or default

    ### Notes

    : Meant for data that may be gone or stale, e.g. read from a cache; a
        wrong config value should raise instead of falling back silently

    """
    return value if check(value) else default


def as_int_or_auto(value, min_value=1):
    """
    Normalize a config or argument value to an int or ```'auto'```.

    Lets a config key or an argument take a fixed number or ```'auto'```,
    where ```'auto'``` leaves the choice to a default of the caller. The
    keyword is matched case-insensitive and without surrounding blanks.

    ### Args

    - **value** (int|str): A number, a number as text or ```'auto'```
    - **min_value** (int, optional): The lowest allowed number. Defaults
        to 1

    ### Returns

    - **int|str**: The number as int, or ```'auto'```

    ### Raises

    - **ValueError**: If the value is neither ```'auto'``` nor an integer
        of at least ```min_value```

    ### Notes

    : ```'auto'``` is not mapped to ```None```, so a caller can still tell
        it apart from an unset value

    """
    if isinstance(value, str) and value.strip().lower() == 'auto':
        return 'auto'
    # bool is an int subclass, but True or False is never meant as number
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        try:
            number = int(value)
        except ValueError:
            pass
        else:
            if number >= min_value:
                return number
    raise ValueError(f'Value {value!r} must be "auto" or an integer >= {min_value}!')


def as_list(value):
    """
    Normalize a single value or a sequence into a list.

    Lets a config key or an argument accept one entry or many: a list or
    tuple comes back as a list, ```None``` as an empty list, and any other
    single value wrapped as ```[value]```.

    ### Args

    - **value**: A single value, a list/tuple of values, or ```None```

    ### Returns

    - **list**: The values as a list (empty for ```None```)

    """
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]
