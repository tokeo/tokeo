"""
Tests for tokeo.core.utils.date and tokeo.core.utils.date_compat.

Two concerns are covered here. First, the date utility functions: each branch
of to_utc, the timestring and datestring formatters, as_utc (and how it differs
from to_utc), and parse_datetimestring_as_utc with its grain detection. Second,
the version compatibility of fromisoformat: that fromisoformat_compat (the
extracted parser, available on every version) agrees with the builtin
datetime.fromisoformat, so the parser used on Python < 3.11 is held to the same
standard as the builtin on 3.11+.
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
    parse_datetimestring_as_utc,
)
from tokeo.core.utils import date_compat


# a fixed offset used to prove shifting vs relabelling
PLUS_TWO = timezone(timedelta(hours=2))


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
    """to_utc turns a date or datetime into a UTC datetime."""

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

    def test_wrong_type_raises(self):
        with pytest.raises(ValueError):
            to_utc(42)


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


class TestAsUtc:
    """as_utc interprets a value as UTC; it relabels a datetime, not shifts it."""

    def test_aware_datetime_is_relabelled_not_shifted(self):
        # the key contrast with to_utc: the wall clock time is KEPT, the offset
        # is dropped and replaced by utc -- 14:00 stays 14:00
        d = datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO)
        assert as_utc(d) == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_contrast_with_to_utc(self):
        # same input, opposite results: as_utc relabels, to_utc shifts
        d = datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO)
        assert as_utc(d).hour == 14
        assert to_utc(d).hour == 12

    def test_naive_datetime_gets_utc_tzinfo(self):
        d = datetime(2026, 6, 23, 14, 0)
        assert as_utc(d) == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_string_is_parsed(self):
        assert as_utc('2026-06-23 14:00:00.000Z') == datetime(
            2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_date_is_lifted_like_to_utc(self):
        d = date(2026, 6, 23)
        assert as_utc(d) == to_utc(d)

    def test_wrong_type_raises(self):
        with pytest.raises(ValueError):
            as_utc(42)


class TestParseDatetimestringAsUtc:
    """parse_datetimestring_as_utc parses ISO strings, optionally by grain.

    A naive string (no offset) is read as local time and converted to UTC, so
    the expected value is derived from the local zone, not assumed to be UTC --
    this keeps the assertions correct on any machine. Deliberate shift cases
    pin the local zone via tzset so the conversion is exercised on purpose.
    """

    @staticmethod
    def _local_to_utc(iso_str):
        # mirror the naive->local->utc chain with stdlib parts, independent of
        # the running machine's timezone, to get the value parse must produce
        return datetime.fromisoformat(iso_str).astimezone(timezone.utc)

    def test_default_always_datetime(self):
        # without auto_type, even a date-only string yields a datetime; a naive
        # input is local, so the expectation is derived from the local zone
        r = parse_datetimestring_as_utc('2026-06-23')
        assert isinstance(r, datetime)
        assert r == self._local_to_utc('2026-06-23')

    def test_auto_type_date_only_yields_date(self):
        # the type is a date; the day is whatever the local->utc shift produced
        r = parse_datetimestring_as_utc('2026-06-23', auto_type=True)
        assert type(r) is date
        assert r == self._local_to_utc('2026-06-23').date()

    def test_auto_type_timestring_yields_datetime(self):
        # an aware string carries its own offset, so the result is fixed
        r = parse_datetimestring_as_utc('2026-06-23 14:00:00.000Z', auto_type=True)
        assert isinstance(r, datetime)
        assert r == datetime(2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_auto_type_length_boundary(self):
        # length 10 is the date boundary; a 10-char date stays a date, an
        # 11-char input (shortest with a time) becomes a datetime
        assert type(parse_datetimestring_as_utc('2026-06-23', auto_type=True)) is date
        assert isinstance(
            parse_datetimestring_as_utc('20260623T12', auto_type=True), datetime)

    def test_aware_string_is_shifted_to_utc(self):
        # an offset in the string is honoured: 14:00 +02:00 -> 12:00 UTC,
        # a fixed result independent of the machine's timezone
        r = parse_datetimestring_as_utc('2026-06-23T14:00:00+02:00')
        assert r == datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)

    def test_naive_input_shifts_with_local_zone(self):
        # deliberate timezone shift: pin the local zone to Tokyo (UTC+9), so a
        # naive midnight is read as Tokyo time and lands on the previous UTC day
        old_tz = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'Asia/Tokyo'
            time.tzset()
            r = parse_datetimestring_as_utc('2026-06-23')
            # 2026-06-23 00:00 in Tokyo (+09:00) is 2026-06-22 15:00 UTC
            assert r == datetime(2026, 6, 22, 15, 0, tzinfo=timezone.utc)
        finally:
            if old_tz is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = old_tz
            time.tzset()

    def test_naive_date_shift_can_change_the_day(self):
        # same deliberate shift, with auto_type: the date itself moves to the
        # neighbouring day, the consequence the docstring warns about
        old_tz = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'Asia/Tokyo'
            time.tzset()
            r = parse_datetimestring_as_utc('2026-06-23', auto_type=True)
            assert r == date(2026, 6, 22)
        finally:
            if old_tz is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = old_tz
            time.tzset()


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
        '2026-06-23T14;00:00',       # invalid time separator
        '2026-06-23T14:00:00.',      # dangling fraction marker
        '2026-06-23T14:00:00.12x4',  # non-digit in the fraction
        '2026-06-23T14:00:00+0',     # malformed (length-1) zone
        '2026-06-23T1',              # incomplete time component
        '2026-0623',                 # inconsistent dash use in the date
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
        assert date_compat.fromisoformat('2026-06-23T14:00:00Z') == datetime(
            2026, 6, 23, 14, 0, tzinfo=timezone.utc)

    def test_star_import_surface(self):
        # __all__ limits the public surface to the two parsers
        assert set(date_compat.__all__) == {'fromisoformat', 'fromisoformat_compat'}
