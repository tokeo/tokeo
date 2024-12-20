from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc)


def to_utc(date):
    return date.astimezone(tz=timezone.utc)


def to_utc_timestring(date):
    date_str = date.astimezone(tz=timezone.utc).isoformat(timespec='milliseconds')
    return date_str[0:10] + ' ' + date_str[11:23] + 'Z'


def to_utc_datestring(date):
    date_str = date.astimezone(tz=timezone.utc).isoformat(timespec='milliseconds')
    return date_str[0:10]


def parse_timestring_as_utc(date_str):
    if date_str.strip() == '':
        raise ValueError('Can not parse empty string as utc timestring.')
    else:
        return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S.%fZ').replace(tzinfo=timezone.utc)


def as_utc(date):
    if isinstance(date, str):
        return parse_timestring_as_utc(date)
    elif isinstance(date, datetime):
        return date.replace(tzinfo=timezone.utc)
    else:
        raise ValueError('Wrong type given for as utc date.')
