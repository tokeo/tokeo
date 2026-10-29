"""
Tests for two concerns of tokeo.core.utils.date and date_compat. First, the
date utility functions: to_utc and as_utc across all their input types (date,
datetime, string, epoch) and their auto_type grain detection, the contrast
between to_utc (which shifts by the offset) and as_utc (which relabels,
keeping the wall clock time), and the timestring and datestring formatters.
Second, the version compatibility of fromisoformat: that fromisoformat_compat
(the extracted parser, available on every version) agrees with the builtin
datetime.fromisoformat, so the parser used on Python < 3.11 is held to the
same standard as the builtin on 3.11+.
"""

import sys
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from tokeo.core.utils.date import (
    utc_now,
    to_utc,
    to_utc_timestring,
    to_utc_datestring,
    as_utc,
)
from tokeo.core.utils import date_compat

# a fixed offset used to prove shifting vs relabelling
PLUS_TWO = timezone(timedelta(hours=2))


@pytest.fixture
def tokyo():
    """The local time zone pinned to Asia/Tokyo for the test."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('TZ', 'Asia/Tokyo')
        time.tzset()
        yield
    # the zone of the environment again for the later tests
    time.tzset()


# ---- utc_now -----------------------------------------------------------------
# utc_now returns an aware UTC datetime at roughly the current instant.


def test_utc_now_is_aware_utc():
    """utc_now returns a datetime with tzinfo UTC."""
    now = utc_now()
    assert isinstance(now, datetime)
    assert now.tzinfo == timezone.utc


def test_utc_now_is_close_to_now():
    """utc_now is within 5 seconds of the real now."""
    delta = abs((utc_now() - datetime.now(timezone.utc)).total_seconds())
    assert delta < 5


# ---- to_utc ------------------------------------------------------------------
# to_utc brings any input to a UTC datetime, shifting by the offset.


def test_to_utc_aware_datetime_is_shifted():
    """14:00 at +02:00 comes back as 12:00 UTC."""
    d = datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO)
    assert to_utc(d) == datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)


def test_to_utc_utc_datetime_is_unchanged():
    """A datetime in UTC comes back equal to itself."""
    d = datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)
    assert to_utc(d) == d


def test_to_utc_date_is_lifted_to_midnight():
    """A date comes back as the datetime of its midnight UTC."""
    result = to_utc(date(2026, 6, 23))
    assert result == datetime(2026, 6, 23, 0, 0, tzinfo=timezone.utc)
    assert isinstance(result, datetime)


def test_to_utc_date_auto_type_stays_date():
    """With auto_type a date comes back as the same date."""
    assert to_utc(date(2026, 6, 23), auto_type=True) == date(2026, 6, 23)
    assert type(to_utc(date(2026, 6, 23), auto_type=True)) is date


def test_to_utc_aware_string_is_shifted():
    """ "14:00+02:00" comes back as 12:00 UTC on every machine."""
    assert to_utc('2026-06-23T14:00:00+02:00') == datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)


def test_to_utc_z_string_is_utc():
    """A string ending in Z is read as UTC."""
    assert to_utc('2026-06-23 14:00:00.000Z') == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)


def test_to_utc_naive_date_string_is_utc_midnight():
    """A date-only string is midnight UTC, without local shift."""
    assert to_utc('2026-06-23') == datetime(2026, 6, 23, 0, 0, tzinfo=timezone.utc)


def test_to_utc_string_auto_type_yields_date():
    """A date-only string with auto_type comes back as that date."""
    r = to_utc('2026-06-23', auto_type=True)
    assert type(r) is date
    assert r == date(2026, 6, 23)


def test_to_utc_string_auto_type_timestring_stays_datetime():
    """A timestring with auto_type comes back as a datetime."""
    r = to_utc('2026-06-23 14:00:00.000Z', auto_type=True)
    assert isinstance(r, datetime)
    assert r == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)


def test_to_utc_string_length_boundary():
    """10 chars with auto_type give a date, 11 chars a datetime."""
    assert type(to_utc('2026-06-23', auto_type=True)) is date
    assert isinstance(to_utc('20260623T12', auto_type=True), datetime)


def test_to_utc_epoch_int_is_utc():
    """The epoch 0 is 1970-01-01 00:00 UTC."""
    assert to_utc(0) == datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc)


def test_to_utc_epoch_float_matches_fromtimestamp():
    """A float epoch equals datetime.fromtimestamp in UTC."""
    ep = 1782744783.413
    assert to_utc(ep) == datetime.fromtimestamp(ep, timezone.utc)


def test_to_utc_naive_timestring_shift_pinned_zone(tokyo):
    """A naive timestring is local time: midnight in Tokyo is 15:00 UTC before."""
    assert to_utc('2026-06-23T00:00:00') == datetime(2026, 6, 22, 15, 0, tzinfo=timezone.utc)


def test_to_utc_bool_is_rejected():
    """True and False raise ValueError, they are no epoch."""
    with pytest.raises(ValueError):
        to_utc(True)
    with pytest.raises(ValueError):
        to_utc(False)


def test_to_utc_wrong_type_raises():
    """None raises ValueError."""
    with pytest.raises(ValueError):
        to_utc(None)


def test_to_utc_short_string_raises():
    """A string under 8 chars raises ValueError."""
    with pytest.raises(ValueError):
        to_utc('2026')


# ---- as_utc ------------------------------------------------------------------
# as_utc relabels any input as UTC, keeping the wall clock time.


def test_as_utc_aware_datetime_is_relabelled_not_shifted():
    """14:00 at +02:00 comes back as 14:00 UTC, the offset is dropped."""
    d = datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO)
    assert as_utc(d) == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)


def test_as_utc_naive_datetime_gets_utc_tzinfo():
    """A naive datetime gets tzinfo UTC with the same wall clock."""
    d = datetime(2026, 6, 23, 14, 0)
    assert as_utc(d) == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)


def test_as_utc_aware_string_is_relabelled():
    """ "14:00+02:00" comes back as 14:00 UTC, like a datetime."""
    assert as_utc('2026-06-23T14:00:00+02:00') == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)


def test_as_utc_string_and_datetime_are_consistent():
    """The same value as string and as datetime gives the same result."""
    s = as_utc('2026-06-23T14:00:00+02:00')
    d = as_utc(datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO))
    assert s == d == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)


def test_as_utc_z_string():
    """A string ending in Z is read as UTC."""
    assert as_utc('2026-06-23 14:00:00.000Z') == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)


def test_as_utc_date_is_lifted_to_midnight():
    """A date comes back as the datetime of its midnight UTC."""
    assert as_utc(date(2026, 6, 23)) == datetime(2026, 6, 23, 0, 0, tzinfo=timezone.utc)


def test_as_utc_date_auto_type_stays_date():
    """With auto_type a date comes back as the same date."""
    assert as_utc(date(2026, 6, 23), auto_type=True) == date(2026, 6, 23)


def test_as_utc_string_auto_type_keeps_the_day(tokyo):
    """A date-only string with auto_type keeps its day in Tokyo too."""
    assert as_utc('2026-06-23', auto_type=True) == date(2026, 6, 23)


def test_as_utc_epoch_is_utc():
    """An epoch equals datetime.fromtimestamp in UTC."""
    ep = 1782744783
    assert as_utc(ep) == datetime.fromtimestamp(ep, timezone.utc)


def test_as_utc_bool_is_rejected():
    """True raises ValueError, it is no epoch."""
    with pytest.raises(ValueError):
        as_utc(True)


def test_as_utc_wrong_type_raises():
    """None raises ValueError."""
    with pytest.raises(ValueError):
        as_utc(None)


# ---- to_utc_vs_as_utc --------------------------------------------------------
# The defining contrast: to_utc shifts, as_utc relabels.


def test_to_utc_vs_as_utc_datetime_offset():
    """For 14:00 at +02:00 to_utc gives hour 12, as_utc hour 14."""
    d = datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO)
    assert to_utc(d).hour == 12  # shifted
    assert as_utc(d).hour == 14  # relabelled


def test_to_utc_vs_as_utc_string_offset():
    """For "14:00+02:00" to_utc gives hour 12, as_utc hour 14."""
    s = '2026-06-23T14:00:00+02:00'
    assert to_utc(s).hour == 12  # shifted
    assert as_utc(s).hour == 14  # relabelled


def test_to_utc_vs_as_utc_string_date_day_under_pinned_zone(tokyo):
    """For a date-only string both keep the day, also in Tokyo."""
    assert to_utc('2026-06-23', auto_type=True) == date(2026, 6, 23)
    assert as_utc('2026-06-23', auto_type=True) == date(2026, 6, 23)


# ---- to_utc_timestring -------------------------------------------------------
# to_utc_timestring formats as 'YYYY-MM-DD HH:MM:SS.MMMZ'.


def test_to_utc_timestring_datetime_format():
    """A UTC datetime formats as "2026-06-23 14:00:00.000Z"."""
    d = datetime(2026, 6, 23, 14, 0, 0, tzinfo=timezone.utc)
    assert to_utc_timestring(d) == '2026-06-23 14:00:00.000Z'


def test_to_utc_timestring_datetime_with_offset_is_shifted():
    """14:00 at +02:00 formats as "... 12:00:00.000Z"."""
    d = datetime(2026, 6, 23, 14, 0, 0, tzinfo=PLUS_TWO)
    assert to_utc_timestring(d) == '2026-06-23 12:00:00.000Z'


def test_to_utc_timestring_date_uses_midnight():
    """A date formats as its midnight, "... 00:00:00.000Z"."""
    assert to_utc_timestring(date(2026, 6, 23)) == '2026-06-23 00:00:00.000Z'


def test_to_utc_timestring_milliseconds_kept():
    """123000 microseconds format as ".123Z"."""
    d = datetime(2026, 6, 23, 14, 0, 0, 123000, tzinfo=timezone.utc)
    assert to_utc_timestring(d) == '2026-06-23 14:00:00.123Z'


def test_to_utc_timestring_wrong_type_raises():
    """An int raises ValueError."""
    with pytest.raises(ValueError):
        to_utc_timestring(42)


# ---- to_utc_datestring -------------------------------------------------------
# to_utc_datestring formats as 'YYYY-MM-DD' (the date part).


def test_to_utc_datestring_datetime():
    """A datetime formats as "2026-06-23"."""
    d = datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)
    assert to_utc_datestring(d) == '2026-06-23'


def test_to_utc_datestring_date():
    """A date formats as "2026-06-23"."""
    assert to_utc_datestring(date(2026, 6, 23)) == '2026-06-23'


def test_to_utc_datestring_offset_can_shift_the_day():
    """00:30 at +02:00 formats as the previous day in UTC."""
    d = datetime(2026, 6, 23, 0, 30, tzinfo=PLUS_TWO)
    assert to_utc_datestring(d) == '2026-06-22'


# ---- fromisoformat_compat ----------------------------------------------------
# fromisoformat_compat must agree with the builtin on every version.


GOOD = [
    '2026-06-23',
    '2026-01-05',
    '2026-06-23T14:00:00',
    '2026-06-23 14:00:00',
    '2026-06-23T14:00:00.1',
    '2026-06-23T14:00:00.12',
    '2026-06-23T14:00:00.123',
    '2026-06-23T14:00:00.123456',
    '2026-06-23T14:00:00.1234567',
    '2026-06-23T14:00:00,123',
    '2026-06-23T14:00:00+00:00',
    '2026-06-23T14:00:00-05:30',
    '2026-06-23T14:00:00+09:00',
    '2026-06-23T14:00:00Z',
    '2026-06-23T14:00:00.123456Z',
    '2026-06-23 14:00:00.000Z',
    '2026-06-23 13:00:00+01:00',
]

BAD = [
    '',
    'foobar',
    '2026',
    '2026-13-01',
    '2026-06-32',
    '2026-06-23T25',
    '2026-06-23xx14:00:00',
    '2026-06-23T14;00:00',  # invalid time separator
    '2026-06-23T14:00:00.',  # dangling fraction marker
    '2026-06-23T14:00:00.12x4',  # non-digit in the fraction
    '2026-06-23T14:00:00+0',  # malformed (length-1) zone
    '2026-06-23T1',  # incomplete time component
    '2026-0623',  # inconsistent dash use in the date
]


@pytest.mark.parametrize('s', GOOD)
def test_fromisoformat_compat_matches_builtin_on_valid(s):
    """For a valid string the compat parser equals the builtin."""
    assert date_compat.fromisoformat_compat(s) == datetime.fromisoformat(s)


@pytest.mark.parametrize('s', BAD)
def test_fromisoformat_compat_matches_builtin_on_invalid(s):
    """For a malformed string both raise ValueError."""
    with pytest.raises(ValueError):
        datetime.fromisoformat(s)
    with pytest.raises(ValueError):
        date_compat.fromisoformat_compat(s)


def test_fromisoformat_compat_rejects_non_string():
    """An int raises TypeError."""
    with pytest.raises(TypeError):
        date_compat.fromisoformat_compat(12345)


# ---- fromisoformat export ----------------------------------------------------
# The version switch picks the right fromisoformat for the running version.


def test_fromisoformat_matches_running_version():
    """On 3.11+ the export is the builtin, below the compat parser."""
    if sys.version_info < (3, 11):
        assert date_compat.fromisoformat is date_compat.fromisoformat_compat
    else:
        # equality (==), not identity: a builtin method access yields a
        # fresh wrapper object each time, so 'is' would not hold
        assert date_compat.fromisoformat == datetime.fromisoformat


def test_fromisoformat_export_parses_trailing_z():
    """The export parses a trailing Z as UTC on every version."""
    assert date_compat.fromisoformat('2026-06-23T14:00:00Z') == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)


def test_fromisoformat_star_import_surface():
    """__all__ holds the two parsers only."""
    assert set(date_compat.__all__) == {'fromisoformat', 'fromisoformat_compat'}
