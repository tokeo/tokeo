"""
Add-years tool for the {{ app_name }} ai agent.

Shifts a date or timestring by a number of years -- forward or, with a
negative number, backward. The day is clamped where needed (february 29 plus
one year is february 28). The grain is kept: a strict ISO date
(```YYYY-MM-DD```) yields a date, a timestring (```YYYY-MM-DD HH:MM:SS.MMMZ```)
yields a timestring. Understanding looser language is the model's job, not the
tool's.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ```ai.tools``` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

import calendar
from datetime import datetime as datetime_type

from tokeo.core.ai import TokeoAiTool
from tokeo.core.ai.tool import create_tool_result
from tokeo.core.utils.date import to_utc, to_utc_datestring, to_utc_timestring


class TokeoAiAddYearsTool(TokeoAiTool):
    """
    Tool that adds a number of years to an ISO date or timestring.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'add a number of years to a date'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(
                date=dict(type='string', description='the date (YYYY-MM-DD)'),
                years=dict(type='integer', description='the years to add (negative goes back)'),
            ),
            required=['date', 'years'],
        )

    def exec(self, date, years):
        """
        Shift a UTC date or timestring by the given years, clamping the day.

        The grain is auto-detected from the input: a date-only string yields a
        date, a timestring yields a datetime. The output keeps that grain -- a
        timestring in yields a timestring out, a plain date yields a plain date.

        ### Args

        - **date** (str): The date as ```YYYY-MM-DD```, or the timestring
            ```YYYY-MM-DD HH:MM:SS.MMMZ```
        - **years** (int): The years to add; a negative number goes back

        ### Returns

        - **str**: The shifted value, ```YYYY-MM-DD``` for a date input or
            ```YYYY-MM-DD HH:MM:SS.MMMZ``` for a timestring input

        """
        d = to_utc(date, auto_type=True)
        # work on the date part: a year shift is a calendar step, not a span
        base = d.date() if isinstance(d, datetime_type) else d
        year = base.year + int(years)
        # clamp the day for the target year (feb 29 + 1y -> feb 28)
        day = min(base.day, calendar.monthrange(year, base.month)[1])
        # replace works on both: a date stays a date, a datetime keeps its time
        shifted = d.replace(year=year, day=day)
        as_str = to_utc_timestring(shifted) if isinstance(d, datetime_type) else to_utc_datestring(shifted)
        return create_tool_result(shifted, as_str=as_str)
