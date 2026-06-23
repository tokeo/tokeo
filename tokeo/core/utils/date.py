from datetime import date as date_type, time as time_type, datetime as datetime_type, timezone
from .date_compat import fromisoformat


def utc_now():
    """
    Get the current UTC datetime.

    ### Returns

    - **datetime**: Current datetime with UTC timezone

    """
    return datetime_type.now(timezone.utc)


def to_utc(date):
    """
    Convert a date or datetime to a UTC datetime.

    A datetime is converted via astimezone(), so the source timezone is
    respected and the wall clock time is shifted accordingly; a naive datetime
    is assumed to be in the local timezone. A date carries no time and no zone,
    so it is lifted to a datetime at midnight UTC.

    ### Args

    - **date** (date | datetime): The value to convert

    ### Returns

    - **datetime**: A UTC datetime; the same instant for a datetime input,
        midnight for a date input

    ### Notes

    : Use this to convert a datetime across timezones. To merely label a
        datetime as UTC without shifting it, use as_utc() instead

    """
    if isinstance(date, datetime_type):
        return date.astimezone(tz=timezone.utc)
    elif isinstance(date, date_type):
        return datetime_type.combine(date, time_type(0, 0), tzinfo=timezone.utc)
    else:
        raise ValueError('Wrong type given as a date.')


def to_utc_timestring(date):
    """
    Convert a date or datetime to a formatted UTC timestring.

    Format: 'YYYY-MM-DD HH:MM:SS.MMMZ'. A datetime is shifted to UTC; a date
    has no time, so midnight UTC is used.

    ### Args

    - **date** (date | datetime): The value to convert

    ### Returns

    - **str**: Formatted UTC timestring

    """
    if isinstance(date, datetime_type):
        date_str = date.astimezone(tz=timezone.utc).isoformat(timespec='milliseconds')
    elif isinstance(date, date_type):
        midnight = datetime_type.combine(date, time_type(0, 0), tzinfo=timezone.utc)
        date_str = midnight.isoformat(timespec='milliseconds')
    else:
        raise ValueError('Wrong type given as a date.')
    return date_str[0:10] + ' ' + date_str[11:23] + 'Z'


def to_utc_datestring(date):
    """
    Convert a date or datetime to a UTC date string.

    Format: 'YYYY-MM-DD'. The date part of the UTC timestring.

    ### Args

    - **date** (date | datetime): The value to convert

    ### Returns

    - **str**: UTC date string (YYYY-MM-DD)

    """
    date_str = to_utc_timestring(date)
    return date_str[0:10]


def as_utc(date):
    """
    Interpret a string, date, or datetime as UTC.

    A string is parsed via parse_datetimestring_as_utc(). A datetime is
    labelled UTC via replace(tzinfo=utc), keeping its wall clock time as-is (an
    aware datetime in another timezone is therefore relabelled, not converted).
    A date carries no time and no zone, so it is lifted to a datetime at
    midnight UTC (via to_utc).

    ### Args

    - **date** (str | date | datetime): The value to interpret as UTC

    ### Returns

    - **datetime**: A UTC datetime; the same wall clock time for a datetime
        input, parsed for a string, midnight for a date input

    ### Raises

    - **ValueError**: If the input type is none of str, date, or datetime

    ### Notes

    : This assumes the value already represents UTC. To convert a datetime
        from another timezone into UTC, use to_utc() instead

    """
    if isinstance(date, str):
        return parse_datetimestring_as_utc(date, auto_type=False)
    elif isinstance(date, datetime_type):
        return date.replace(tzinfo=timezone.utc)
    elif isinstance(date, date_type):
        return to_utc(date)
    else:
        raise ValueError('Wrong type given for as utc date.')


def parse_datetimestring_as_utc(datetime_str, auto_type=False):
    """
    Parse an ISO 8601 string into a UTC datetime, or a date.

    Accepts any ISO 8601 form fromisoformat handles. With auto_type the result
    follows the input's grain: a date-only string (length <= 10) yields a date,
    anything with a time yields a datetime. Without it a datetime is always
    returned. The value is read via to_utc, so an aware string is converted to
    UTC and a naive string is taken as local time (and thus depends on the
    server's timezone).

    ### Args

    - **datetime_str** (str): The ISO 8601 string to parse
    - **auto_type** (bool): When True, return a date for a date-only input;
        otherwise always return a datetime

    ### Returns

    - **date | datetime**: A datetime carrying UTC tzinfo, or a date when
        auto_type is set and the input carried no time

    ### Raises

    - **ValueError**: If the input is not a string, is empty, or does not
        match a supported ISO 8601 form

    """
    d = to_utc(fromisoformat(datetime_str))
    return d if not auto_type or len(datetime_str) > 10 else d.date()
