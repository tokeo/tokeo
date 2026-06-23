"""
A version-stable datetime.fromisoformat for Python 3.10+.

Python 3.10's datetime.fromisoformat parses only the narrow set its own
isoformat() emits: no trailing ```Z```, no compact forms, fractional seconds
only at 3 or 6 digits. Python 3.11 widened it to most of ISO 8601. This module
papers over that gap so the same input parses the same way on every supported
version.

It exports two names:

- **fromisoformat**: on Python < 3.11 the extracted parser below, on 3.11+ the
    builtin (faster, C-implemented, already wide enough). Import this for normal
    use -- it is the right one for the running version.
- **fromisoformat_compat**: always the extracted parser, on every version. Import
    this in a test to compare the extracted parser against the builtin and prove
    they agree on 3.11+, so the parser used on 3.10 is held to the same standard.

The parser is extracted from CPython 3.12 Lib/_pydatetime.py (the pure-python
reference the C build matches), trimmed to the forms these tools use: a plain
date, a date-time with a ```T``` or space separator, fractional seconds, a
numeric offset, and a trailing ```Z```. ISO week dates (```YYYY-Www-D```) are not
supported.
"""

import sys
from datetime import datetime, timezone, timedelta


# the public surface: a star-import gets only these two, not the imported
# modules or the _helpers (which the underscore would hide anyway)
__all__ = [
    'fromisoformat',
    'fromisoformat_compat',
]


def _is_ascii_digit(c):
    return c in '0123456789'


def _find_isoformat_datetime_separator(dtstr):
    """
    Find the index that splits the date part from the time part.

    Trimmed from the CPython helper: only the plain-date forms are handled
    (```YYYY-MM-DD``` at length 10, the date-only length-7/8 inputs handed in by
    fromisoformat below), since week dates are out of scope here.

    """
    # a bare date string (length 7 from a 'YYYY-DDD'-style slice, or the common
    # 10-char 'YYYY-MM-DD') has no separator to find; the date branch consumes
    # the whole string and the time part stays empty
    if len(dtstr) <= 10:
        return len(dtstr)
    # otherwise the separator sits right after the 'YYYY-MM-DD' date
    return 10


def _parse_isoformat_date(dtstr):
    """
    Parse the date part 'YYYY-MM-DD' (or 'YYYYMMDD') into [year, month, day].

    """
    year = int(dtstr[0:4])
    has_sep = dtstr[4] == '-'
    pos = 4 + has_sep
    month = int(dtstr[pos:pos + 2])
    pos += 2
    if (dtstr[pos:pos + 1] == '-') != has_sep:
        raise ValueError('Inconsistent use of dash separator')
    pos += has_sep
    day = int(dtstr[pos:pos + 2])
    return [year, month, day]


_FRACTION_CORRECTION = [100000, 10000, 1000, 100, 10]


def _parse_hh_mm_ss_ff(tstr):
    """
    Parse 'HH[:?MM[:?SS[{.,}fff[fff]]]]' into [hour, minute, second, micro].

    Accepts any number of fractional digits (truncating beyond six), which is
    exactly the widening 3.11 brought and 3.10 lacks.

    """
    len_str = len(tstr)
    time_comps = [0, 0, 0, 0]
    pos = 0
    for comp in range(0, 3):
        if (len_str - pos) < 2:
            raise ValueError('Incomplete time component')
        time_comps[comp] = int(tstr[pos:pos + 2])
        pos += 2
        next_char = tstr[pos:pos + 1]
        if comp == 0:
            has_sep = next_char == ':'
        if not next_char or comp >= 2:
            break
        if has_sep and next_char != ':':
            raise ValueError('Invalid time separator: %c' % next_char)
        pos += has_sep
    if pos < len_str:
        if tstr[pos] not in '.,':
            raise ValueError('Invalid microsecond component')
        else:
            pos += 1
            len_remainder = len_str - pos
            to_parse = 6 if len_remainder >= 6 else len_remainder
            time_comps[3] = int(tstr[pos:(pos + to_parse)])
            if to_parse < 6:
                time_comps[3] *= _FRACTION_CORRECTION[to_parse - 1]
            if (len_remainder > to_parse
                    and not all(map(_is_ascii_digit, tstr[(pos + to_parse):]))):
                raise ValueError('Non-digit values in unparsed fraction')
    return time_comps


def _parse_isoformat_time(tstr):
    """
    Parse the time part with its optional offset into time + tzinfo components.

    Handles a trailing ```Z``` as UTC (the 3.10 builtin does not) and a numeric
    ```+HH:MM``` offset; an all-zero offset is normalized to UTC.

    """
    len_str = len(tstr)
    if len_str < 2:
        raise ValueError('Isoformat time too short')
    # find the offset start: first '-', '+', or 'Z' (find()+1 turns -1 into 0)
    tz_pos = (tstr.find('-') + 1 or tstr.find('+') + 1 or tstr.find('Z') + 1)
    timestr = tstr[:tz_pos - 1] if tz_pos > 0 else tstr
    time_comps = _parse_hh_mm_ss_ff(timestr)
    tzi = None
    if tz_pos == len_str and tstr[-1] == 'Z':
        tzi = timezone.utc
    elif tz_pos > 0:
        tzstr = tstr[tz_pos:]
        # valid offsets: HH, HHMM, HH:MM, HHMMSS, HH:MM:SS (+ optional fraction)
        if len(tzstr) in (0, 1, 3):
            raise ValueError('Malformed time zone string')
        tz_comps = _parse_hh_mm_ss_ff(tzstr)
        if all(x == 0 for x in tz_comps):
            tzi = timezone.utc
        else:
            tzsign = -1 if tstr[tz_pos - 1] == '-' else 1
            td = timedelta(hours=tz_comps[0], minutes=tz_comps[1],
                           seconds=tz_comps[2], microseconds=tz_comps[3])
            tzi = timezone(tzsign * td)
    time_comps.append(tzi)
    return time_comps


def fromisoformat_compat(date_string):
    """
    Parse an ISO 8601 string into a datetime, the same way on every version.

    A standalone re-implementation of the CPython 3.12 parser (slimmed to drop
    week dates), so its result does not depend on the running Python version.
    Always available; the version-appropriate ```fromisoformat``` below is what
    normal code should import.

    ### Args

    - **date_string** (str): The ISO 8601 string to parse (a date, or a
        date-time with a ```T``` or space separator, optional fractional seconds
        and a numeric offset or trailing ```Z```)

    ### Returns

    - **datetime**: The parsed datetime (aware when the string carried an offset
        or ```Z```, naive otherwise)

    ### Raises

    - **TypeError**: If the argument is not a string
    - **ValueError**: If the string is not a supported ISO 8601 form

    """
    if not isinstance(date_string, str):
        raise TypeError('fromisoformat: argument must be str')
    if len(date_string) < 7:
        raise ValueError(f'Invalid isoformat string: {date_string!r}')
    try:
        separator_location = _find_isoformat_datetime_separator(date_string)
        dstr = date_string[0:separator_location]
        tstr = date_string[(separator_location + 1):]
        date_components = _parse_isoformat_date(dstr)
    except ValueError:
        raise ValueError(f'Invalid isoformat string: {date_string!r}') from None
    if tstr:
        try:
            time_components = _parse_isoformat_time(tstr)
        except ValueError:
            raise ValueError(
                f'Invalid isoformat string: {date_string!r}') from None
    else:
        time_components = [0, 0, 0, 0, None]
    return datetime(*(date_components + time_components))


# on 3.10 the builtin is too narrow (no Z, narrow forms), so use the extracted
# parser; on 3.11+ the builtin already parses the wide set and is the faster
# C implementation, so prefer it. fromisoformat_compat above stays the extracted
# parser on every version, so a test can hold the builtin to the same standard
if sys.version_info < (3, 11):
    fromisoformat = fromisoformat_compat
else:
    fromisoformat = datetime.fromisoformat
