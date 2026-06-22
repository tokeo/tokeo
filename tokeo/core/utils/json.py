import json
import datetime
import dataclasses
import functools
from . import date


def jsonTokeoEncoder(obj, ignore_unknown=True):
    """
    Custom JSON encoder function for Tokeo types.

    The ```default``` hook for ```json.dumps```: json calls it for any value it
    cannot serialize itself. It converts dates to standardized strings and
    unpacks a dataclass to a dict (so json walks its fields). For any other
    object it either returns ```None``` -- the default, letting json render it as
    ```null``` -- or, when ```ignore_unknown``` is ```False```, the object's type
    name as a readable fallback.

    ### Args

    - **obj** (any): The object to encode
    - **ignore_unknown** (bool): For an object this encoder does not handle,
        return ```None``` (json renders ```null```) when ```True``` (the
        default), or its type name as a string when ```False```. Set ```False```
        when a readable placeholder is wanted (e.g. a trace step's ```origin```
        handler/guard, which is an attribution, not data)

    ### Returns

    - **str|dict|None**: A string for a date/datetime; a dict for a dataclass; a
        type-name string or ```None``` for any other object, per
        ```ignore_unknown```

    ### Notes

    - datetime objects are checked first and converted via
        date.to_utc_timestring() to 'YYYY-MM-DD HH:MM:SS.MMMZ' format
    - date objects are converted to 'YYYY-MM-DD' format
    - a dataclass is unpacked one level via __dict__ so json recurses through its
        fields -- not dataclasses.asdict, which deepcopies the whole graph and
        dies on a field holding a live object (such as a trace step's origin)

    """
    # check datetime before date: datetime is a subclass of date, so a
    # date-first test would also catch datetimes and drop their time part
    if isinstance(obj, datetime.datetime):
        return date.to_utc_timestring(obj)
    # a plain date carries no time or tzinfo, so format it directly; routing
    # it through as_utc() would raise since as_utc only accepts str/datetime
    if isinstance(obj, datetime.date):
        return obj.strftime('%Y-%m-%d')
    # a dataclass: hand json a shallow dict of its fields so json recurses
    # through them (not asdict, which deepcopies and dies on a live origin)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dict(obj.__dict__)
    # an unknown object: None by default (json renders null), or a readable
    # type name when ignore_unknown is off
    return None if ignore_unknown else type(obj).__name__


def jsonDump(obj, default=jsonTokeoEncoder, encoding=None, ignore_unknown=True, **kwargs):
    """
    Serialize object to JSON string with Tokeo-specific type handling.

    This is a wrapper around json.dumps() that uses the jsonTokeoEncoder
    by default and provides optional encoding to bytes.

    ### Args

    - **obj** (any): The object to serialize to JSON
    - **default** (callable, optional): A function that gets called for
        objects that can't be serialized. Defaults to jsonTokeoEncoder.
    - **encoding** (str, optional): If provided, the resulting JSON string
        will be encoded to bytes using this encoding (e.g., 'utf-8')
    - **ignore_unknown** (bool): Passed to the default jsonTokeoEncoder: when
        ```False```, render an unhandled object as its type name instead of
        ```null```. Has no effect when a custom ```default``` is given. Default
        ```True```
    - ****kwargs**: Forwarded to json.dumps (e.g. indent, sort_keys)

    ### Returns

    - **str|bytes**: JSON string or bytes if encoding is specified

    ### Notes

    : This function maintains proper serialization of Tokeo-specific types like
        dates and datetimes by using the jsonTokeoEncoder as the default encoder.

    """
    # only the built-in encoder takes the flag; a custom default is used as is
    if default is jsonTokeoEncoder and not ignore_unknown:
        default = functools.partial(jsonTokeoEncoder, ignore_unknown=False)
    j = json.dumps(obj, default=default, **kwargs)
    return j.encode(encoding) if encoding else j
