from datetime import date as date_type, time as time_type, datetime as datetime_type, timezone
from .date_compat import fromisoformat


def utc_now():
    """
    Get the current UTC datetime.

    ### Returns

    - **datetime**: Current datetime with UTC timezone

    """
    return datetime_type.now(timezone.utc)


def to_utc(date, auto_type=False):
    """
    Convert a date, datetime, string, or epoch timestamp to a UTC datetime.

    Every input is brought to the same UTC instant: a datetime is converted via
    astimezone(), so its source timezone is respected and the wall clock time
    is shifted accordingly (a naive datetime is assumed to be local time); a
    date is lifted to midnight UTC, as it carries no time or zone; a string is
    parsed as ISO 8601 and shifted by its offset (a naive string is taken as
    local time, so its result depends on the server's timezone); an epoch
    timestamp (seconds since 1970, int or float) is read directly as UTC, since
    an epoch is an absolute instant.

    With auto_type the result follows the input's grain: a date input or a
    date-only string (length <= 10) yields a date, everything else a datetime.
    Without it the result is always a datetime.

    ### Args

    - **date** (date | datetime | str | int | float): The value to convert; a
        string is an ISO 8601 timestring, a number a POSIX epoch in seconds
    - **auto_type** (bool): When True, return a date for a date or a date-only
        string; otherwise always return a datetime

    ### Returns

    - **date | datetime**: A UTC datetime carrying tzinfo, or a date when
        auto_type is set and the input carried no time

    ### Raises

    - **ValueError**: If the input type is none of the above, or the string is
        not a supported ISO 8601 form

    ### Notes

    : Use this to bring any supported value to UTC. To merely label a datetime
        as UTC without shifting it, use as_utc() instead

    """
    if isinstance(date, datetime_type):
        return date.astimezone(tz=timezone.utc)
    elif isinstance(date, date_type):
        d = date if auto_type else datetime_type.combine(date, time_type(0, 0), tzinfo=timezone.utc)
        # with auto_type a date input stays a date else a utc datetime midnight
        return d
    elif isinstance(date, str) and len(date) >= 8:
        # parse the ISO string, then bring it to UTC inline (a recursive to_utc
        # call is avoided by handling the parsed datetime or date here)
        parsed = fromisoformat(date)
        if isinstance(parsed, datetime_type):
            # if date string was a datetime then move into utc, if it was
            # only a date then replace it to utc
            d = parsed.astimezone(tz=timezone.utc) if len(date) > 10 else parsed.replace(tzinfo=timezone.utc)
        elif isinstance(parsed, date_type):
            d = datetime_type.combine(parsed, time_type(0, 0), tzinfo=timezone.utc)
        else:
            raise ValueError('Wrong string given as a date.')
        # a datetime, except auto_type with a date-only string yields a date
        return d if not auto_type or len(date) > 10 else d.date()
    elif isinstance(date, (int, float)) and not isinstance(date, bool):
        # an epoch is an absolute instant, so read it straight as UTC
        return datetime_type.fromtimestamp(date, tz=timezone.utc)
    else:
        raise ValueError('Wrong type or data given as a date.')


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


def as_utc(date, auto_type=False):
    """
    Interpret a date, datetime, string, or epoch timestamp as already UTC.

    The wall clock time is kept and merely labelled UTC, the source offset is
    dropped, not converted: a datetime is relabelled via replace(tzinfo=utc), so
    14:00+02:00 becomes 14:00 UTC; a string is parsed and its wall clock time
    relabelled the same way; a date is lifted to midnight UTC, as it carries no
    time or zone; an epoch timestamp (seconds since 1970, int or float) is an
    absolute instant, so it is read directly as UTC (here relabelling and
    converting coincide).

    With auto_type the result follows the input's grain: a date input or a
    date-only string (length <= 10) yields a date, everything else a datetime.
    Without it the result is always a datetime.

    ### Args

    - **date** (date | datetime | str | int | float): The value to interpret as
        UTC; a string is an ISO 8601 timestring, a number a POSIX epoch
    - **auto_type** (bool): When True, return a date for a date or a date-only
        string; otherwise always return a datetime

    ### Returns

    - **date | datetime**: A UTC datetime keeping the input's wall clock time,
        or a date when auto_type is set and the input carried no time

    ### Raises

    - **ValueError**: If the input type is none of the above, or the string is
        not a supported ISO 8601 form

    ### Notes

    : This assumes the value already represents UTC. To convert a datetime from
        another timezone into UTC, use to_utc() instead

    """
    if isinstance(date, datetime_type):
        return date.replace(tzinfo=timezone.utc)
    elif isinstance(date, date_type):
        d = datetime_type.combine(date, time_type(0, 0), tzinfo=timezone.utc)
        # with auto_type a date input stays a date
        return d if not auto_type else d.date()
    elif isinstance(date, str) and len(date) >= 8:
        # parse the ISO string, then relabel its wall clock time as UTC, so the
        # source offset is dropped rather than converted
        parsed = fromisoformat(date)
        if isinstance(parsed, datetime_type):
            d = parsed.replace(tzinfo=timezone.utc)
        elif isinstance(parsed, date_type):
            d = datetime_type.combine(parsed, time_type(0, 0), tzinfo=timezone.utc)
        else:
            raise ValueError('Wrong string given as a date.')
        # a datetime, except auto_type with a date-only string yields a date
        return d if not auto_type or len(date) > 10 else d.date()
    elif isinstance(date, (int, float)) and not isinstance(date, bool):
        # an epoch is an absolute instant, so read it straight as UTC
        return datetime_type.fromtimestamp(date, tz=timezone.utc)
    else:
        raise ValueError('Wrong type or data given as a date.')
