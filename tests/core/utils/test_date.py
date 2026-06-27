"""
Tests for tokeo.core.utils.date and tokeo.core.utils.date_compat.

Two concerns are covered here. First, the date utility functions: to_utc and
as_utc across all their input types (date, datetime, string, epoch) and their
auto_type grain detection, the contrast between to_utc (which shifts by the
offset) and as_utc (which relabels, keeping the wall clock time), and the
timestring and datestring formatters. Second, the version compatibility of
fromisoformat: that fromisoformat_compat (the extracted parser, available on
every version) agrees with the builtin datetime.fromisoformat, so the parser
used on Python < 3.11 is held to the same standard as the builtin on 3.11+.
"""

import os
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


class _PinnedZone:
    """Context manager that pins the local timezone via TZ + tzset."""

    def __init__(self, tz_name):
        self._tz_name = tz_name
        self._old = None

    def __enter__(self):
        self._old = os.environ.get('TZ')
        os.environ['TZ'] = self._tz_name
        time.tzset()
        return self

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = self._old
        time.tzset()
        return False


class TestUtcNow:
    """utc_now returns an aware UTC datetime at roughly the current instant."""

    def test_is_aware_utc(self):
        now = utc_now()
        assert isinstance(now, datetime)
        assert now.tzinfo == timezone.utc

    def test_is_close_to_now(self):
        # the call should land within a generous window of the real now
        delta = abs((utc_now() - datetime.now(timezone.utc)).total_seconds())
        assert delta < 5


class TestToUtc:
    """to_utc brings any input to a UTC datetime, shifting by the offset."""

    def test_aware_datetime_is_shifted(self):
        # 14:00 at +02:00 is 12:00 UTC -- the instant is recomputed
        d = datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO)
        assert to_utc(d) == datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)

    def test_utc_datetime_is_unchanged(self):
        d = datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)
        assert to_utc(d) == d

    def test_date_is_lifted_to_midnight(self):
        # a date has no time, so it becomes midnight UTC as a datetime
        result = to_utc(date(2026, 6, 23))
        assert result == datetime(2026, 6, 23, 0, 0, tzinfo=timezone.utc)
        assert isinstance(result, datetime)

    def test_date_auto_type_stays_date(self):
        # with auto_type a date input keeps its grain
        assert to_utc(date(2026, 6, 23), auto_type=True) == date(2026, 6, 23)
        assert type(to_utc(date(2026, 6, 23), auto_type=True)) is date

    def test_aware_string_is_shifted(self):
        # an offset in the string is honoured: 14:00 +02:00 -> 12:00 UTC,
        # a fixed result independent of the machine's timezone
        assert to_utc('2026-06-23T14:00:00+02:00') == datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)

    def test_z_string_is_utc(self):
        assert to_utc('2026-06-23 14:00:00.000Z') == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_naive_date_string_is_utc_midnight(self):
        # a date-only string carries no time or zone, so it is taken as midnight
        # UTC directly -- no local shift, the same day on every machine
        assert to_utc('2026-06-23') == datetime(2026, 6, 23, 0, 0, tzinfo=timezone.utc)

    def test_string_auto_type_yields_date(self):
        # a date-only string with auto_type yields a date, taken as utc midnight
        # so the day is kept (no local shift)
        r = to_utc('2026-06-23', auto_type=True)
        assert type(r) is date
        assert r == date(2026, 6, 23)

    def test_string_auto_type_timestring_stays_datetime(self):
        r = to_utc('2026-06-23 14:00:00.000Z', auto_type=True)
        assert isinstance(r, datetime)
        assert r == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_string_length_boundary(self):
        # length 10 is the date boundary; a 10-char date stays a date, an
        # 11-char input (shortest with a time) becomes a datetime
        assert type(to_utc('2026-06-23', auto_type=True)) is date
        assert isinstance(to_utc('20260623T12', auto_type=True), datetime)

    def test_epoch_int_is_utc(self):
        assert to_utc(0) == datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc)

    def test_epoch_float_matches_fromtimestamp(self):
        ep = 1782744783.413
        assert to_utc(ep) == datetime.fromtimestamp(ep, timezone.utc)

    def test_naive_timestring_shift_pinned_zone(self):
        # a naive timestring is read as local time: midnight in Tokyo (+09:00)
        # lands on the previous UTC day; pinning the zone makes the shift explicit
        with _PinnedZone('Asia/Tokyo'):
            assert to_utc('2026-06-23T00:00:00') == datetime(2026, 6, 22, 15, 0, tzinfo=timezone.utc)

    def test_bool_is_rejected(self):
        # bool is an int subtype, but must not be read as an epoch
        with pytest.raises(ValueError):
            to_utc(True)
        with pytest.raises(ValueError):
            to_utc(False)

    def test_wrong_type_raises(self):
        with pytest.raises(ValueError):
            to_utc(None)

    def test_short_string_raises(self):
        # a string under 8 chars is too short to be an ISO date
        with pytest.raises(ValueError):
            to_utc('2026')


class TestAsUtc:
    """as_utc relabels any input as UTC, keeping the wall clock time."""

    def test_aware_datetime_is_relabelled_not_shifted(self):
        # the offset is dropped, the wall clock kept -- 14:00 stays 14:00
        d = datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO)
        assert as_utc(d) == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_naive_datetime_gets_utc_tzinfo(self):
        d = datetime(2026, 6, 23, 14, 0)
        assert as_utc(d) == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_aware_string_is_relabelled(self):
        # the key fix: a string offset is dropped, not converted -- 14:00+02:00
        # relabels to 14:00 UTC, consistent with the datetime branch
        assert as_utc('2026-06-23T14:00:00+02:00') == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_string_and_datetime_are_consistent(self):
        # same value as string and as datetime must give the same result
        s = as_utc('2026-06-23T14:00:00+02:00')
        d = as_utc(datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO))
        assert s == d == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_z_string(self):
        assert as_utc('2026-06-23 14:00:00.000Z') == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_date_is_lifted_to_midnight(self):
        assert as_utc(date(2026, 6, 23)) == datetime(2026, 6, 23, 0, 0, tzinfo=timezone.utc)

    def test_date_auto_type_stays_date(self):
        assert as_utc(date(2026, 6, 23), auto_type=True) == date(2026, 6, 23)

    def test_string_auto_type_keeps_the_day(self):
        # because as_utc relabels (not shifts), a date-only string with
        # auto_type keeps its day on every machine, unlike to_utc
        with _PinnedZone('Asia/Tokyo'):
            assert as_utc('2026-06-23', auto_type=True) == date(2026, 6, 23)

    def test_epoch_is_utc(self):
        # for an epoch, relabelling and converting coincide
        ep = 1782744783
        assert as_utc(ep) == datetime.fromtimestamp(ep, timezone.utc)

    def test_bool_is_rejected(self):
        with pytest.raises(ValueError):
            as_utc(True)

    def test_wrong_type_raises(self):
        with pytest.raises(ValueError):
            as_utc(None)


class TestToUtcVsAsUtc:
    """The defining contrast: to_utc shifts, as_utc relabels."""

    def test_datetime_offset(self):
        # same input, opposite results on the wall clock
        d = datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO)
        assert to_utc(d).hour == 12  # shifted
        assert as_utc(d).hour == 14  # relabelled

    def test_string_offset(self):
        s = '2026-06-23T14:00:00+02:00'
        assert to_utc(s).hour == 12  # shifted
        assert as_utc(s).hour == 14  # relabelled

    def test_string_date_day_under_pinned_zone(self):
        # for a date-only string to_utc no longer shifts the day: it now agrees
        # with as_utc and keeps the day, on every machine
        with _PinnedZone('Asia/Tokyo'):
            assert to_utc('2026-06-23', auto_type=True) == date(2026, 6, 23)
            assert as_utc('2026-06-23', auto_type=True) == date(2026, 6, 23)


class TestToUtcTimestring:
    """to_utc_timestring formats as 'YYYY-MM-DD HH:MM:SS.MMMZ'."""

    def test_datetime_format(self):
        d = datetime(2026, 6, 23, 14, 0, 0, tzinfo=timezone.utc)
        assert to_utc_timestring(d) == '2026-06-23 14:00:00.000Z'

    def test_datetime_with_offset_is_shifted(self):
        d = datetime(2026, 6, 23, 14, 0, 0, tzinfo=PLUS_TWO)
        assert to_utc_timestring(d) == '2026-06-23 12:00:00.000Z'

    def test_date_uses_midnight(self):
        assert to_utc_timestring(date(2026, 6, 23)) == '2026-06-23 00:00:00.000Z'

    def test_milliseconds_kept(self):
        d = datetime(2026, 6, 23, 14, 0, 0, 123000, tzinfo=timezone.utc)
        assert to_utc_timestring(d) == '2026-06-23 14:00:00.123Z'

    def test_wrong_type_raises(self):
        with pytest.raises(ValueError):
            to_utc_timestring(42)


class TestToUtcDatestring:
    """to_utc_datestring formats as 'YYYY-MM-DD' (the date part)."""

    def test_datetime(self):
        d = datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)
        assert to_utc_datestring(d) == '2026-06-23'

    def test_date(self):
        assert to_utc_datestring(date(2026, 6, 23)) == '2026-06-23'

    def test_offset_can_shift_the_day(self):
        # 00:30 at +02:00 is 22:30 the previous day in UTC
        d = datetime(2026, 6, 23, 0, 30, tzinfo=PLUS_TWO)
        assert to_utc_datestring(d) == '2026-06-22'


class TestFromisoformatParity:
    """fromisoformat_compat must agree with the builtin on every version."""

    # forms within the slim parser's scope (no week dates)
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
    def test_compat_matches_builtin_on_valid(self, s):
        # fromisoformat_compat is the extracted parser on every version; it must
        # produce exactly what the builtin produces for a valid ISO string
        assert date_compat.fromisoformat_compat(s) == datetime.fromisoformat(s)

    @pytest.mark.parametrize('s', BAD)
    def test_compat_matches_builtin_on_invalid(self, s):
        # both must reject the same malformed inputs with a ValueError
        with pytest.raises(ValueError):
            datetime.fromisoformat(s)
        with pytest.raises(ValueError):
            date_compat.fromisoformat_compat(s)

    def test_compat_rejects_non_string(self):
        with pytest.raises(TypeError):
            date_compat.fromisoformat_compat(12345)


class TestFromisoformatExport:
    """The version switch picks the right fromisoformat for the running version."""

    def test_fromisoformat_matches_running_version(self):
        # on 3.11+ the export is the builtin; on < 3.11 it is the extracted
        # parser. either way it must parse the broad set the same way
        if sys.version_info < (3, 11):
            assert date_compat.fromisoformat is date_compat.fromisoformat_compat
        else:
            # equality (==), not identity: a builtin method access yields a
            # fresh wrapper object each time, so 'is' would not hold
            assert date_compat.fromisoformat == datetime.fromisoformat

    def test_export_parses_trailing_z(self):
        # the whole point of the compat layer: Z parses on every version
        assert date_compat.fromisoformat('2026-06-23T14:00:00Z') == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_star_import_surface(self):
        # __all__ limits the public surface to the two parsers
        assert set(date_compat.__all__) == {'fromisoformat', 'fromisoformat_compat'}
