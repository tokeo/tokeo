"""
Tokeo Base Module.

Hand back a value, or a default when the value is missing, empty or blank.
"""


def value_or_default(value, default=None):
    """
    Return default value if the input is None, blank, or empty.

    Works with different types:

    - For strings: checks if None, empty or only whitespace
    - For containers (list, dict, tuple, set): checks if empty
    - For other types: returns the value if not None

    ### Args

    - **value** (any): Value to check
    - **default** (any, optional): Value to return if input is empty

    ### Returns

    - **any**: Original value or default value

    """
    if value is None:
        return default

    # fast path returns on instance types
    if isinstance(value, str):
        if str.strip(value) == '':
            return default
        else:
            return value

    # fast path returns on instance types
    if isinstance(value, (list, dict, tuple, set)):
        if len(value) == 0:
            return default
        else:
            return value

    # seems to be filled
    return value


def filled_str_or_default(value, default=None):
    """
    Return default value if the input string is None or blank.

    A string is considered blank if it is empty or contains only whitespace.

    ### Args

    - **value** (str): String value to check
    - **default** (any, optional): Value to return if input is None or blank

    ### Returns

    - **str|any**: Original string or default value

    ### Raises

    - **TypeError**: If value is not None and not a string

    """
    if value is None:
        return default

    # test correct instance type
    if not isinstance(value, str):
        raise TypeError('filled_str_or_default expects a string as input arg')

    if str.strip(value) == '':
        return default

    # seems to be filled
    return value


def first_value_or_default(values, default=None):
    """
    Return the first non-None value from a list of values.

    ### Args

    - **values** (list|tuple): List of values to check
    - **default** (any, optional): Value to return if all values are None

    ### Returns

    - **any**: First non-None value or default value

    ### Raises

    - **TypeError**: If values is not a list or tuple

    """
    if not isinstance(values, (list, tuple)):
        raise TypeError('first_value_or_default expects a list or tuple as input arg')

    for value in values:
        if value is not None:
            return value

    return default


def first_filled_str_or_default(values, default=None):
    """
    Return the first non-blank string from a list of values.

    A string is considered blank if it is empty or contains only whitespace.

    ### Args

    - **values** (list|tuple): List of string values to check
    - **default** (any, optional): Value to return if no non-blank strings found

    ### Returns

    - **str|any**: First non-blank string or default value

    ### Raises

    - **TypeError**: If values is not a list or tuple, or if any value
        is not a string

    """
    if not isinstance(values, (list, tuple)):
        raise TypeError('first_filled_str_or_default expects a list or tuple as input arg')

    for value in values:
        if value is not None:
            # test correct instance type
            if not isinstance(value, str):
                raise TypeError('first_filled_str_or_default expects strings as values')

            if str.strip(value) != '':
                return value

    return default
