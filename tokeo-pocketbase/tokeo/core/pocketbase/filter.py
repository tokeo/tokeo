"""
Values from outside put safely into a request: into a filter, quoted and
escaped for the filter syntax of PocketBase, and into a segment of a path.

"""

import re
from datetime import datetime, timezone
from urllib.parse import quote
from .exc import TokeoPocketBaseError

_PLACEHOLDER = re.compile(r'\{:(\w+)\}')


def _filter_value(value):
    # a value as literal of the filter syntax of PocketBase; bool before int,
    # bool is an int in python
    if isinstance(value, str):
        # the filter parser knows the escapes \\ and \' in a quoted string
        return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, datetime):
        # pocketbase stores datetimes in utc, a naive one is taken as utc
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return "'" + value.strftime('%Y-%m-%d %H:%M:%S.') + f'{value.microsecond // 1000:03d}Z' + "'"
    raise TokeoPocketBaseError(f'filter value of type {type(value).__name__} is not supported: {value!r}')


def format_filter(expr, params=None):
    """
    Build a filter expression with its placeholders replaced by values.

    ### Args

    - **expr** (str): The filter with placeholders like ```{:title}```
    - **params** (dict): The values of the placeholders

    ### Returns

    - **str**: The filter with each value quoted and escaped

    ### Raises

    - **TokeoPocketBaseError**: If a placeholder has no value or a value is
        of an unsupported type

    ### Notes

    : Supported values: str, bool, None, int, float and datetime

    : Each placeholder is replaced once, a value that looks like a
        placeholder is never replaced again

    ### Example

    ```python
    format_filter('title = {:t} && views > {:v}', {'t': "it's", 'v': 3})
    # title = 'it\\'s' && views > 3
    ```

    """
    params = params or {}

    def replace(match):
        name = match.group(1)
        if name not in params:
            raise TokeoPocketBaseError(f'filter placeholder {{:{name}}} has no value')
        return _filter_value(params[name])

    return _PLACEHOLDER.sub(replace, expr)


def quote_path_segment(value):
    """
    Quote a value for one segment of a path of the api.

    ### Args

    - **value** (Any): The value, e.g. the id of a record

    ### Returns

    - **str**: The value quoted, so it cannot leave its segment

    ### Example

    ```python
    pb.send('GET', f'/api/collections/{quote_path_segment(name)}/records')
    ```

    """
    return quote(str(value), safe='')
