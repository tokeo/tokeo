import json
import datetime
from . import date


def jsonTokeoEncoder(obj):
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
    j = json.dumps(obj, default=default)
    return j.encode(encoding) if encoding else j
