import json
import datetime
from . import date


def jsonTokeoEncoder(obj):
    """
    Custom JSON encoder function for Tokeo types.

    Converts datetime.date and datetime.datetime objects to standardized
    string formats.

    ### Args:

    - **obj** (any): The object to encode

    ### Returns:

    - **str**: String representation of the object if it's a date/datetime
    - **None**: If the object type is not supported by this encoder

    ### Notes:

    1. date objects are converted to 'YYYY-MM-DD' format in UTC
    1. datetime objects are converted using date.to_utc_timestring()
        to 'YYYY-MM-DD HH:MM:SS.MMMZ' format
    1. Returns None for other types, signaling to the JSON encoder to use
        the default encoding

    """
    # prepare a date as string representation
    if isinstance(obj, (datetime.date)):
        return date.as_utc(obj).strftime('%Y-%m-%d')
    # prepare a datetime as string representation
    if isinstance(obj, (datetime.datetime)):
        return date.to_utc_timestring(obj)
    # return None to flag that the Encoder could
    # not handle the object
    return None


def jsonDump(obj, default=jsonTokeoEncoder, encoding=None):
    """
    Serialize object to JSON string with Tokeo-specific type handling.

    This is a wrapper around json.dumps() that uses the jsonTokeoEncoder
    by default and provides optional encoding to bytes.

    ### Args:

    - **obj** (any): The object to serialize to JSON
    - **default** (callable, optional): A function that gets called for
      bjects that can't be serialized. Defaults to jsonTokeoEncoder.
    - **encoding** (str, optional): If provided, the resulting JSON string
      will be encoded to bytes using this encoding (e.g., 'utf-8')

    ### Returns:

    - **str|bytes**: JSON string or bytes if encoding is specified

    ### Notes:

    : This function maintains proper serialization of Tokeo-specific types like
      dates and datetimes by using the jsonTokeoEncoder as the default encoder.

    """
    j = json.dumps(obj, default=default)
    return j.encode(encoding) if encoding else j
