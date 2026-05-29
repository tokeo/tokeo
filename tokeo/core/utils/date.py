from datetime import datetime, timezone


def utc_now():
    """
    Get the current UTC datetime.

    ### Returns

    - **datetime**: Current datetime with UTC timezone

    """
    return datetime.now(timezone.utc)


def to_utc(date):
    """
    Convert a datetime to UTC by recomputing the instant.

    Uses astimezone(), so the source timezone is respected and the wall
    clock time is shifted accordingly; a naive datetime is assumed to be
    in the local timezone.

    ### Args

    - **date** (datetime): The datetime to convert

    ### Returns

    - **datetime**: The same instant expressed in UTC

    ### Notes

    : Use this to convert across timezones. To merely label a datetime as
        UTC without shifting it, use as_utc() instead

    """
    return date.astimezone(tz=timezone.utc)


def to_utc_timestring(date):
    """
    Convert a datetime to a formatted UTC timestring.

    Format: 'YYYY-MM-DD HH:MM:SS.MMMZ'

    ### Args

    - **date** (datetime): The datetime to convert

    ### Returns

    - **str**: Formatted UTC timestring

    """
    date_str = date.astimezone(tz=timezone.utc).isoformat(timespec='milliseconds')
    return date_str[0:10] + ' ' + date_str[11:23] + 'Z'


def to_utc_datestring(date):
    """
    Convert a datetime to a UTC date string.

    Format: 'YYYY-MM-DD'

    ### Args

    - **date** (datetime): The datetime to convert

    ### Returns

    - **str**: UTC date string (YYYY-MM-DD)

    """
    date_str = date.astimezone(tz=timezone.utc).isoformat(timespec='milliseconds')
    return date_str[0:10]


def parse_timestring_as_utc(date_str):
    """
    Parse a formatted timestring into a UTC datetime.

    Expects format: 'YYYY-MM-DD HH:MM:SS.MMMZ'

    ### Args

    - **date_str** (str): The timestring to parse

    ### Returns

    - **datetime**: Parsed datetime with UTC timezone

    ### Raises

    - **ValueError**: If the input string is empty or None

    """
    if date_str is None or date_str.strip() == '':
        raise ValueError('Can not parse empty string as utc timestring.')
    else:
        return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S.%fZ').replace(tzinfo=timezone.utc)


def as_utc(date):
    """
    Label a string or datetime as UTC without shifting the clock time.

    For a datetime this uses replace(tzinfo=utc), so the wall clock time
    is kept as-is and only the tzinfo is set. An aware datetime in another
    timezone is therefore relabelled, not converted. A string is parsed
    via parse_timestring_as_utc().

    ### Args

    - **date** (str|datetime): The value to interpret as UTC

    ### Returns

    - **datetime**: The value carrying UTC tzinfo, same wall clock time

    ### Raises

    - **ValueError**: If the input type is neither str nor datetime

    ### Notes

    : This assumes the value already represents UTC. To convert a datetime
        from another timezone into UTC, use to_utc() instead

    """
    if isinstance(date, str):
        return parse_timestring_as_utc(date)
    elif isinstance(date, datetime):
        return date.replace(tzinfo=timezone.utc)
    else:
        raise ValueError('Wrong type given for as utc date.')
