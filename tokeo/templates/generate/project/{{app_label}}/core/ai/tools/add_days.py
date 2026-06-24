"""
Add-days tool for the {{ app_name }} ai agent.

Shifts a date or timestring by a number of days -- forward or, with a
negative number, backward. The grain is kept: a strict ISO date
(```YYYY-MM-DD```) yields a date, a timestring (```YYYY-MM-DD HH:MM:SS.MMMZ```)
yields a timestring. Understanding looser language is the model's job, not
the tool's.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ```ai.tools``` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

from datetime import datetime as datetime_type, timedelta
from tokeo.core.ai import TokeoAiTool
from tokeo.core.ai.tool import create_tool_result
from tokeo.core.utils.date import to_utc, to_utc_datestring, to_utc_timestring


class TokeoAiAddDaysTool(TokeoAiTool):
    """
    Tool that adds a number of days to an ISO date or timestring.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'add a number of days to a date'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(
                date=dict(type='string', description='the date (YYYY-MM-DD)'),
                days=dict(type='integer', description='the days to add (negative goes back)'),
            ),
            required=['date', 'days'],
        )

    def exec(self, date, days):
        """
        Shift a UTC date or timestring by the given days.

        The grain is auto-detected from the input: a date-only string yields a
        date, a timestring yields a datetime. The output keeps that grain -- a
        timestring in yields a timestring out, a plain date yields a plain date.

        ### Args

        - **date** (str): The date as ```YYYY-MM-DD```, or the timestring
            ```YYYY-MM-DD HH:MM:SS.MMMZ```
        - **days** (int): The days to add; a negative number goes back

        ### Returns

        - **str**: The shifted value, ```YYYY-MM-DD``` for a date input or
            ```YYYY-MM-DD HH:MM:SS.MMMZ``` for a timestring input

        """
        # the shift to apply, forward or (for a negative number) back
        td = timedelta(days=int(days))
        # parse with grain detection, then keep that grain across the shift
        d = to_utc(date, auto_type=True) + td
        # a datetime renders as a timestring, a date as a date string
        as_str = to_utc_timestring(d) if isinstance(d, datetime_type) else to_utc_datestring(d)
        return create_tool_result(d, as_str=as_str)
